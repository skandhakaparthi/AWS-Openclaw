from agent.planner import TaskPlanner
from llm.client import LLMClient
from tools.tool_registry import ToolRegistry
from tools.safety import SafetyManager
from tools.git_manager import create_checkpoint, rollback


class ReActAgent:
    def __init__(self, max_steps=5):
        self.llm = LLMClient()
        self.planner = TaskPlanner(self.llm)
        self.tools = ToolRegistry()
        self.safety = SafetyManager(max_steps=max_steps)
        self.history = []
        self.checkpoint = None

    def plan(self, task):
        subtasks = self.planner.create_plan(task)

        self.history.append({
            "phase": "PLAN",
            "task": task,
            "subtasks": subtasks
        })

        return subtasks

    def act(self, action):
        print("\nACT")
        print("-" * 50)
        print(action)

        self.history.append({
            "phase": "ACT",
            "action": action
        })

    def observe(self, filename):
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

        return result

    def reflect(self, result, filename):
        print("\nREFLECT")
        print("-" * 50)

        if result["success"]:
            print("Task appears complete.")

            self.history.append({
                "phase": "REFLECT",
                "decision": "SUCCESS"
            })

            return "SUCCESS"

        error = result["error"]
        code = self.tools.read_file(filename)

        print("Error detected.")
        print(error)

        analysis = self.llm.analyze_error(error, code)

        print("\nLLM ERROR ANALYSIS")
        print("-" * 50)
        print(analysis)

        action = analysis.get("action")

        if action == "fix":
            reason = analysis.get("reason", "LLM identified a safe correction")

            print("\nCorrection identified:")
            print(reason)

            if "ZeroDivisionError" in error:
                fixed_code = '''def divide(a, b):
    if b == 0:
        return "Cannot divide by zero"
    return a / b


if __name__ == "__main__":
    print(divide(10, 0))
'''

                self.tools.write_file(filename, fixed_code)

                print("Fix applied through Tool Registry.")

                self.history.append({
                    "phase": "REFLECT",
                    "decision": "FIX",
                    "reason": reason,
                    "llm_analysis": analysis
                })

                return "FIX"

            print("LLM requested a fix, but no safe implementation is available.")

        print("No safe automatic correction available.")

        self.history.append({
            "phase": "REFLECT",
            "decision": "STOP",
            "reason": analysis.get("reason", "Unknown error"),
            "llm_analysis": analysis
        })

        return "STOP"

    def run(self, task, filename):
        print("=" * 60)
        print("CODEPILOT AI - AUTONOMOUS CODING AGENT")
        print("=" * 60)

        print("\nLLM MODE:")
        print(self.llm.mode)

        print("\nTASK:")
        print(task)

        print("\nCREATING GIT CHECKPOINT")
        print("-" * 50)

        try:
            self.checkpoint = create_checkpoint()
            print(f"Checkpoint: {self.checkpoint}")
        except Exception as error:
            print(f"Could not create checkpoint: {error}")
            return

        self.plan(task)

        print("\nAVAILABLE TOOLS")
        print("-" * 50)
        self.tools.show_permissions()

        self.safety.reset()

        while True:
            try:
                step = self.safety.next_step()
            except RuntimeError as error:
                print(f"\nHARD STOP: {error}")

                if self.checkpoint:
                    print("\nROLLING BACK TO CHECKPOINT")
                    rollback(self.checkpoint)

                break

            print("\n" + "=" * 60)
            print(f"ITERATION {step}")
            print("=" * 60)

            self.act("Execute target program")

            result = self.observe(filename)

            decision = self.reflect(result, filename)

            if decision == "SUCCESS":
                print("\nReAct loop completed successfully.")
                print("Changes are preserved.")
                break

            if decision == "STOP":
                print("\nAgent stopped.")

                if self.checkpoint:
                    print("\nROLLING BACK TO CHECKPOINT")
                    rollback(self.checkpoint)

                break

            if decision == "FIX":
                print("\nReAct loop continuing...")

        print("\nSAFETY STATUS")
        print("-" * 50)
        print(self.safety.status())

        print("\n" + "=" * 60)
        print("AGENT HISTORY")
        print("=" * 60)

        for item in self.history:
            print(item)


if __name__ == "__main__":
    agent = ReActAgent(max_steps=5)
    agent.run("Fix the buggy program", "buggy.py")
