import uuid
import ast
from agent.planner import TaskPlanner
from agent.state import TaskState
from llm.client import LLMClient
from tools.tool_registry import ToolRegistry
from tools.safety import SafetyManager
from tools.git_manager import create_checkpoint, rollback


class ReActAgent:

    def __init__(self, max_steps=5):

        self.llm = LLMClient()
        self.planner = TaskPlanner(self.llm)
        self.tools = ToolRegistry()

        self.safety = SafetyManager(
            max_steps=max_steps,
            max_cost=0.05,
            max_runtime_seconds=60,
            repeated_error_limit=2
        )

        self.history = []
        self.checkpoint = None
        self.task_state = None

    def plan(self, task):

        subtasks = self.planner.create_plan(task)

        self.history.append({
            "phase": "PLAN",
            "task": task,
            "subtasks": subtasks
        })

        return subtasks

    def act(self, action, iteration):

        print("\nACT")
        print("-" * 50)
        print(action)

        self.history.append({
            "phase": "ACT",
            "action": action
        })

        self.task_state.record_iteration(
            iteration,
            "ACT",
            action
        )

    def observe(self, filename, iteration):

        result = self.tools.run_python(filename)

        print("\nOBSERVE")
        print("-" * 50)

        if result["success"]:
            print("Execution successful.")

            if result["output"]:
                print("Output:")
                print(result["output"])
        else:
            print("Execution failed.")
            print("Error:")
            print(result["error"])

        self.history.append({
            "phase": "OBSERVE",
            "result": result
        })

        self.task_state.record_iteration(
            iteration,
            "OBSERVE",
            result
        )

        return result

    
    def reflect(self, result, filename, iteration):

        print("\nREFLECT")
        print("-" * 50)

        # A successful execution means the task is complete.
        if result.get("success", False):

            print("Task appears complete.")

            if result.get("output"):
                print("Validated output:")
                print(result["output"])

            self.history.append({
                "phase": "REFLECT",
                "decision": "SUCCESS",
                "reason": "Program executed successfully"
            })

            self.task_state.record_iteration(
                iteration,
                "REFLECT",
                "SUCCESS"
            )

            return "SUCCESS"
        error = result.get("error", "")

        print("Error detected:")
        print(error)

        # Enforce the sandbox timeout policy directly.
        if result.get("return_code") == -1 and "timed out" in error.lower():
            print("Timeout detected. Stopping for rollback.")

            self.history.append({
                "phase": "REFLECT",
                "decision": "STOP",
                "reason": "Execution timeout; rollback required"
            })

            self.task_state.record_iteration(
                iteration,
                "REFLECT",
                {
                    "decision": "STOP",
                    "reason": "Execution timeout; rollback required"
                }
            )

            return "STOP"

        code = self.tools.read_file(filename)
        self.safety.record_error(error)

        analysis = self.llm.analyze_error(
            error,
            code
        )

        print("\nLLM ERROR ANALYSIS")
        print("-" * 50)
        print(analysis)

        self.task_state.record_iteration(
            iteration,
            "REFLECT",
            analysis
        )

        action = analysis.get("action")

        if action == "fix":

            reason = analysis.get(
                "reason",
                "LLM identified a correction"
            )

            print("\nCorrection identified:")
            print(reason)

            
            fixed_code = self.llm.generate_fix(error, code)

            if not fixed_code:
                print("No safe fix was generated.")
                self.history.append({
                    "phase": "REFLECT",
                    "decision": "STOP",
                    "reason": "No fix generated"
                })
                return "STOP"


            try:
                ast.parse(fixed_code)
            except SyntaxError as exc:
                print(f"Generated fix has invalid syntax: {exc}")
                self.history.append({
                    "phase": "REFLECT",
                    "decision": "STOP",
                    "reason": "Generated code failed syntax validation"
                })
                return "STOP"

            self.tools.write_file(
                filename,
                fixed_code
            )

            print("Fix applied through Tool Registry.")

            self.history.append({
                "phase": "REFLECT",
                "decision": "FIX",
                "reason": reason,
                "llm_analysis": analysis
            })

            return "FIX"


        print("No safe automatic correction available.")

        self.history.append({
            "phase": "REFLECT",
            "decision": "STOP",
            "reason": analysis.get(
                "reason",
                "Unknown error"
            ),
            "llm_analysis": analysis
        })

        return "STOP"

    def rollback_to_checkpoint(self):

        if self.checkpoint:

            print(
                "\nROLLING BACK TO CHECKPOINT"
            )
            print("-" * 50)

            try:

                rollback(
                    self.checkpoint
                )

                print(
                    "Rollback completed successfully."
                )

            except Exception as error:

                print(
                    f"Rollback failed: {error}"
                )

    def run(self, task, filename):

        task_id = "task-" + uuid.uuid4().hex[:8]

        self.task_state = TaskState(task_id)

        print("=" * 60)
        print(
            "CODEPILOT AI - AUTONOMOUS CODING AGENT"
        )
        print("=" * 60)

        print("\nTASK ID:")
        print(task_id)

        print("\nLLM MODE:")
        print(self.llm.mode)

        print("\nTASK:")
        print(task)

        print("\nCREATING GIT CHECKPOINT")
        print("-" * 50)

        try:

            self.checkpoint = create_checkpoint()

            print(
                f"Checkpoint: {self.checkpoint}"
            )

        except Exception as error:

            print(
                f"Could not create checkpoint: {error}"
            )

            self.task_state.set_status(
                "FAILED"
            )

            self.task_state.save()

            return

        self.plan(task)

        self.task_state.record_iteration(
            0,
            "PLAN",
            self.history[-1]
        )

        print("\nAVAILABLE TOOLS")
        print("-" * 50)

        self.tools.show_permissions()

        self.safety.reset()

        print("\nSAFETY LIMITS")
        print("-" * 50)

        print(
            f"Maximum iterations: "
            f"{self.safety.max_steps}"
        )

        print(
            f"Maximum estimated cost: "
            f"${self.safety.max_cost:.4f}"
        )

        print(
            f"Maximum runtime: "
            f"{self.safety.max_runtime_seconds} seconds"
        )

        print(
            f"Repeated error limit: "
            f"{self.safety.repeated_error_limit}"
        )

        while True:

            try:

                step = self.safety.next_step()

                print(
                    "\n" + "=" * 60
                )

                print(
                    f"ITERATION {step}"
                )

                print(
                    "=" * 60
                )

                self.act(
                    "Execute target program",
                    step
                )

                result = self.observe(
                    filename,
                    step
                )

                # Mock mode has zero cost.
                # Bedrock integration can later provide
                # a real estimated cost here.
                self.safety.add_cost(0.00)

                self.task_state.add_cost(
                    0.00
                )

                decision = self.reflect(
                    result,
                    filename,
                    step
                )

                if decision == "SUCCESS":

                    print(
                        "\nReAct loop completed successfully."
                    )

                    print(
                        "Changes are preserved."
                    )

                    self.task_state.set_status(
                        "SUCCESS"
                    )

                    break

                if decision == "STOP":

                    print(
                        "\nAgent stopped."
                    )

                    self.task_state.set_status(
                        "ROLLED_BACK"
                    )

                    self.rollback_to_checkpoint()

                    break

                if decision == "FIX":

                    print(
                        "\nReAct loop continuing..."
                    )

            except RuntimeError as error:

                print(
                    "\nSAFETY STOP"
                )

                print("-" * 50)

                print(
                    f"Reason: {error}"
                )

                self.history.append({
                    "phase": "SAFETY",
                    "decision": "STOP",
                    "reason": str(error)
                })

                self.task_state.record_iteration(
                    self.safety.current_step,
                    "SAFETY",
                    str(error)
                )

                self.task_state.set_status(
                    "ROLLED_BACK"
                )

                self.rollback_to_checkpoint()

                break

            except Exception as error:

                print(
                    "\nUNEXPECTED AGENT ERROR"
                )

                print("-" * 50)

                print(
                    f"Error: {error}"
                )

                self.history.append({
                    "phase": "ERROR",
                    "error": str(error)
                })

                self.task_state.record_iteration(
                    self.safety.current_step,
                    "ERROR",
                    str(error)
                )

                self.task_state.set_status(
                    "ROLLED_BACK"
                )

                self.rollback_to_checkpoint()

                break

        print("\nSAFETY STATUS")
        print("-" * 50)

        print(
            self.safety.status()
        )

        print("\nSAVING TASK STATE")
        print("-" * 50)

        state_file = self.task_state.save()

        print(
            f"State saved to: {state_file}"
        )

        print("\nFINAL TASK STATE")
        print("-" * 50)

        self.task_state.show()

        print("\n" + "=" * 60)
        print("AGENT HISTORY")
        print("=" * 60)

        for item in self.history:
            print(item)


if __name__ == "__main__":

    agent = ReActAgent(
        max_steps=5
    )

    agent.run(
        "Fix the buggy program",
        "repair_test.py"
    )
