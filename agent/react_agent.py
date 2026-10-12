import uuid
import ast
import hashlib
from pathlib import Path
from tools.executor import PROJECT_ROOT
from agent.task_workspace import TaskWorkspaceManager
from agent.planner import TaskPlanner
from agent.state import TaskState
from llm.client import LLMClient, LLMProviderError
from tools.tool_registry import ToolRegistry
from tools.safety import SafetyManager
from tools.filesystem import WORKSPACE


class ReActAgent:

    def __init__(self, max_steps=5, workspace=None, llm_client=None, state_dir=None):

        self.llm = llm_client or LLMClient()
        self.planner = TaskPlanner(self.llm)
        self.requested_workspace = Path(workspace).resolve() if workspace else None
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.tools = ToolRegistry(workspace=self.requested_workspace)

        self.safety = SafetyManager(
            max_steps=max_steps,
            max_cost=0.05,
            max_runtime_seconds=60,
            repeated_error_limit=2
        )

        self.history = []
        self.task_state = None
        self._original_task_files = {}
        self._expected_task_files = {}

    def plan(self, task):

        subtasks = self.planner.create_plan(task)

        self.history.append({
            "phase": "PLAN",
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

            if result["output"] and self.llm.mode != "bedrock":
                print("Output:")
                print(result["output"])
        else:
            print("Execution failed.")
            if self.llm.mode != "bedrock":
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
        self.task_state.state["latest_execution_result"] = result
        self.task_state.state["tool_invocations"].append({"tool": "run_python", "filename": filename})

        return result

    
    def reflect(self, result, filename, iteration):

        print("\nREFLECT")
        print("-" * 50)

        if not isinstance(result, dict):
            result = {"success": False, "error": "Missing execution result", "execution_mode": "unknown"}
        error = str(result.get("error") or "")
        mode = str(result.get("execution_mode") or "")
        return_code = int(result.get("return_code", -2))
        if result.get("timed_out") or return_code == -1:
            classification = "TIMEOUT"
        elif mode in {"docker-unavailable", "image-missing", "docker-error"}:
            classification = "INFRA_ERROR"
        elif mode in {"mode-rejected", "path-rejected"}:
            classification = "PERMISSION_ERROR"
        elif result.get("success") is True and return_code == 0:
            classification = "SUCCESS"
        elif return_code == -2:
            classification = "INFRA_ERROR"
        elif "syntaxerror" in error.lower():
            classification = "SYNTAX_ERROR"
        elif "assertionerror" in error.lower():
            classification = "ASSERTION_FAILURE"
        elif any(token in error.lower() for token in ("error", "exception")):
            classification = "RUNTIME_ERROR"
        else:
            classification = "CODE_ERROR"
        self.task_state.state["error_classification"] = classification
        self.task_state.state["latest_execution_result"] = result
        if classification != "SUCCESS":
            self.task_state.state["latest_test_result"] = {
                "verified": False, "return_code": return_code, "error": error
            }

        if classification == "SUCCESS":
            self.task_state.state["latest_test_result"] = {
                "verified": True, "return_code": 0,
                "output": result.get("output", "")
            }

            print("Task appears complete.")

            if result.get("output") and self.llm.mode != "bedrock":
                print("Validated output:")
                print(result["output"])

            self.history.append({
                "phase": "REFLECT",
                "decision": "SUCCESS",
                "reason": "Executed acceptance check exited with code 0",
                "acceptance_check": {"verified": True, "return_code": 0}
            })

            self.task_state.record_iteration(
                iteration,
                "REFLECT",
                {"decision": "SUCCESS", "acceptance_check": {"verified": True, "return_code": 0}}
            )

            return "SUCCESS"
        if classification in {"INFRA_ERROR", "PERMISSION_ERROR", "TIMEOUT"}:
            self.task_state.state["latest_test_result"] = {"verified": False, "reason": classification}
            self.history.append({"phase": "REFLECT", "decision": classification, "error": error})
            self.task_state.record_iteration(iteration, "REFLECT", {"decision": classification, "error": error})
            return classification

        print("Error detected; details omitted from logs." if self.llm.mode == "bedrock" else "Error detected:")
        if self.llm.mode != "bedrock":
            print(error)

        # Enforce the sandbox timeout policy directly.
        if result.get("return_code") == -1 and "timed out" in error.lower():
            print("Timeout detected. Stopping safely.")

            self.history.append({
                "phase": "REFLECT",
                "decision": "STOP",
                "reason": "Execution timeout; task stopped"
            })

            self.task_state.record_iteration(
                iteration,
                "REFLECT",
                {
                    "decision": "STOP",
                    "reason": "Execution timeout; task stopped"
                }
            )

            return "STOP"

        source = self.tools.read_file(filename)
        if str(source).startswith("ERROR:"):
            self.task_state.state["error_classification"] = "PERMISSION_ERROR"
            return "PERMISSION_ERROR"
        self.task_state.state["previous_error_signatures"].append(
            hashlib.sha256(error.strip().encode("utf-8", errors="replace")).hexdigest()
        )
        code = source
        self.task_state.state["files_inspected"].append(filename)
        try:
            self.safety.record_error(error)
        except RuntimeError:
            self.task_state.state["error_classification"] = "REPEATED_ERROR"
            return "MAX_ITERATIONS"

        try:
            analysis = self.llm.analyze_error(error, code)
        except LLMProviderError as provider_error:
            return self._record_provider_failure(provider_error, iteration)

        print("\nLLM ERROR ANALYSIS")
        print("-" * 50)
        print("Structured analysis received.")

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

            print("\nCorrection identified; details omitted from logs." if self.llm.mode == "bedrock" else "\nCorrection identified:")
            if self.llm.mode != "bedrock":
                print(reason)

            
            try:
                fixed_code = self.llm.generate_fix(error, code)
            except LLMProviderError as provider_error:
                return self._record_provider_failure(provider_error, iteration)

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

            self._original_task_files.setdefault(filename, code)
            self._expected_task_files[filename] = fixed_code
            write_result = self.tools.write_file(
                filename,
                fixed_code
            )
            verified_source = self.tools.read_file(filename)
            if (isinstance(write_result, str) and write_result.startswith("ERROR:")) or verified_source != fixed_code:
                self.task_state.state["error_classification"] = "PERMISSION_ERROR"
                return "PERMISSION_ERROR"
            if filename not in self.task_state.state["files_changed"]:
                self.task_state.state["files_changed"].append(filename)
            self.task_state.state["tool_invocations"].append({"tool": "write_file", "filename": filename})

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

    def _record_provider_failure(self, error, iteration):
        self.task_state.state["error_classification"] = "PROVIDER_ERROR"
        self.task_state.state["provider_error"] = {
            "kind": error.kind,
            "message": str(error),
        }
        self.history.append({
            "phase": "LLM_PROVIDER",
            "decision": "STOP",
            "kind": error.kind,
            "message": str(error),
        })
        self.task_state.record_iteration(iteration, "LLM_PROVIDER", {
            "decision": "STOP", "kind": error.kind, "message": str(error),
        })
        return "PROVIDER_ERROR"

    def rollback_task_changes(self):
        """Restore only agent-written files, refusing to overwrite later edits."""
        restored = []
        conflicts = []
        for filename, original in self._original_task_files.items():
            current = self.tools.read_file(filename)
            expected = self._expected_task_files.get(filename)
            if current == original:
                continue
            if current != expected:
                conflicts.append(filename)
                continue
            result = self.tools.write_file(filename, original)
            verified = self.tools.read_file(filename)
            if (isinstance(result, str) and result.startswith("ERROR:")) or verified != original:
                conflicts.append(filename)
            else:
                restored.append(filename)
        return {
            "verified": not conflicts,
            "restored_files": restored,
            "conflicts": conflicts,
        }

    def run(self, task, filename):
        task_id = "task-" + uuid.uuid4().hex[:8]
        if self.requested_workspace is not None:
            return self._run_in_workspace(task, filename, task_id)

        manager = TaskWorkspaceManager(WORKSPACE, PROJECT_ROOT / ".agent_tasks")
        manager.create(task_id)
        prior_state_dir = self.state_dir
        if self.state_dir is None:
            self.state_dir = manager.artifact_root / "_task_state"
        self.requested_workspace = manager.workspace
        self.tools = ToolRegistry(workspace=self.requested_workspace)
        try:
            summary = self._run_in_workspace(task, filename, task_id)
            artifact = None
            if self.task_state.state.get("status") == "SUCCESS":
                artifact = manager.create_review_artifact(
                    task_id,
                    self.task_state.state.get("files_changed", []),
                    self.task_state.state["status"],
                    self.task_state.state.get("latest_test_result"),
                )
                self.task_state.state["review_artifact"] = artifact
                summary["review_artifact"] = artifact
            self.task_state.state["final_summary"] = summary
            self.task_state.state["cleanup_result"] = manager.cleanup()
            if not self.task_state.state["cleanup_result"]["success"]:
                self.task_state.state["cleanup_error"] = self.task_state.state["cleanup_result"].get("error")
                self.task_state.set_status("CLEANUP_FAILED")
                summary["status"] = "CLEANUP_FAILED"
            self.task_state.save()
            return summary
        except BaseException as error:
            cleanup = manager.cleanup()
            if self.task_state is not None:
                self.task_state.set_status("CANCELLED" if isinstance(error, KeyboardInterrupt) else "FAILED")
                self.task_state.state["cleanup_result"] = cleanup
                if not cleanup["success"]:
                    self.task_state.state["cleanup_error"] = cleanup.get("error")
                self.task_state.save()
            raise
        finally:
            self.requested_workspace = None
            self.state_dir = prior_state_dir
            self.tools = ToolRegistry()

    def _run_in_workspace(self, task, filename, task_id):
        self.task_state = TaskState(task_id, original_request=task, state_dir=self.state_dir)
        self.task_state.set_status("RUNNING")
        self._original_task_files = {}
        self._expected_task_files = {}
        self.task_state.state["workspace"] = str(self.requested_workspace)
        self.safety.reset()

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
        print("Task content omitted from logs.")

        print("\nTASK WORKSPACE")
        print("-" * 50)
        print(self.requested_workspace)

        try:
            self.plan(task)
        except LLMProviderError as provider_error:
            self._record_provider_failure(provider_error, 0)
            self.task_state.set_status("FAILED")
            self.task_state.state["final_summary"] = {
                "status": "FAILED",
                "error_classification": "PROVIDER_ERROR",
                "provider_error": self.task_state.state["provider_error"],
                "files_modified": [],
                "estimated_model_cost_usd": None,
                "cost_limit_enforced": False,
                "model_usage": self.llm.usage_summary(),
                "workspace": str(self.requested_workspace),
            }
            self.task_state.save()
            return self.task_state.state["final_summary"]
        self.task_state.state["plan"] = self.history[-1]["subtasks"]

        self.task_state.record_iteration(
            0,
            "PLAN",
            self.history[-1]
        )

        print("\nAVAILABLE TOOLS")
        print("-" * 50)

        self.tools.show_permissions()

        print("\nSAFETY LIMITS")
        print("-" * 50)

        print(
            f"Maximum iterations: "
            f"{self.safety.max_steps}"
        )

        print("Maximum estimated cost: unmeasured (provider usage data unavailable)")

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
                self.task_state.state["latest_execution_result"] = result
                self.safety.check_runtime()

                decision = self.reflect(
                    result,
                    filename,
                    step
                )
                self.safety.check_runtime()

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

                if decision not in {"FIX", "SUCCESS"}:

                    print(
                        "\nAgent stopped."
                    )

                    self.task_state.set_status("FAILED")

                    break

                if decision == "FIX":

                    print(
                        "\nReAct loop continuing..."
                    )

            except RuntimeError as error:

                message = str(error)
                if "iteration limit" in message.lower():
                    self.task_state.state["error_classification"] = "MAX_ITERATIONS"
                elif "runtime limit" in message.lower():
                    self.task_state.state["error_classification"] = "TIMEOUT"
                elif "cost limit" in message.lower():
                    self.task_state.state["error_classification"] = "BUDGET_EXHAUSTED"

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

                self.task_state.set_status("FAILED")

                break

            except PermissionError as error:

                self.task_state.state["error_classification"] = "PERMISSION_ERROR"
                self.task_state.record_iteration(
                    self.safety.current_step, "PERMISSION", str(error)
                )
                self.task_state.set_status("FAILED")
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

                self.task_state.set_status("FAILED")

                break

        print("\nSAFETY STATUS")
        print("-" * 50)

        print(
            self.safety.status()
        )

        print("\nSAVING TASK STATE")
        print("-" * 50)

        if (self.task_state.state["status"] == "FAILED" and
                self.task_state.state.get("error_classification") in {
                    "RUNTIME_ERROR", "ASSERTION_FAILURE", "SYNTAX_ERROR",
                    "CODE_ERROR", "REPEATED_ERROR", "MAX_ITERATIONS", "PROVIDER_ERROR"
                }):
            rollback_result = self.rollback_task_changes()
            self.task_state.state["rollback_result"] = rollback_result
            self.task_state.state["rollback_occurred"] = bool(rollback_result["restored_files"])
            if rollback_result["verified"]:
                self.task_state.set_status("ROLLED_BACK")

        self.task_state.state["final_summary"] = {
            "status": self.task_state.state["status"],
            "files_modified": self.task_state.state.get("files_changed", []),
            "tests_executed": [filename],
            "execution_result": self.task_state.state.get("latest_execution_result"),
            "rollback_occurred": self.task_state.state.get("rollback_occurred", False),
            "rollback_result": self.task_state.state.get("rollback_result"),
            "estimated_model_cost_usd": None,
            "cost_limit_enforced": False,
            "model_usage": self.llm.usage_summary(),
            "workspace": str(self.requested_workspace),
        }
        state_file = self.task_state.save()

        print(
            f"State saved to: {state_file}"
        )

        print("\nFINAL TASK STATE")
        print("-" * 50)

        if self.llm.mode == "bedrock":
            print(f"Status: {self.task_state.state['status']}; details saved without prompt text.")
        else:
            self.task_state.show()

        print("\n" + "=" * 60)
        print("AGENT HISTORY")
        print("=" * 60)

        if self.llm.mode == "bedrock":
            print(f"Recorded {len(self.history)} agent events; details omitted from logs.")
        else:
            for item in self.history:
                print(item)

        return self.task_state.state["final_summary"]


if __name__ == "__main__":

    agent = ReActAgent(
        max_steps=5
    )

    agent.run(
        "Fix the buggy program",
        "repair_test.py"
    )
