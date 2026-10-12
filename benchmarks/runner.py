"""Execute the benchmark suite through ReActAgent and its sandboxed tools."""
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.react_agent import ReActAgent
from llm.client import LLMClient

SUITE = ROOT / "benchmarks" / "benchmark_suite.json"
SCENARIOS = {
    "BENCH-001": ("factorial.py", "SUCCESS"),
    "BENCH-002": ("repair_loop_temp.py", "SUCCESS"),
    "BENCH-003": ("unknown_variable.py", "FAILED"),
    "BENCH-004": ("repair_loop_temp.py", "FAILED"),
}
SCENARIO_PURPOSE = {
    "BENCH-001": "correct program execution",
    "BENCH-002": "code generation and repair after a runtime error",
    "BENCH-003": "safe refusal and isolated-workspace discard after an unknown-name failure",
    "BENCH-004": "repeated equivalent error stopping",
}


class _RepeatErrorLLM(LLMClient):
    """Deterministic benchmark double that proposes no-op repairs."""
    def __init__(self):
        super().__init__(mode="mock")

    def analyze_error(self, error, code):
        return {"action": "fix", "reason": "repeat-error benchmark", "confidence": 1.0}

    def generate_fix(self, error, code):
        return code


def run_one(task, source_workspace, timeout=60):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codepilot-bench-") as temp:
        workspace = Path(temp) / "workspace"
        shutil.copytree(source_workspace, workspace)
        if task["id"] == "BENCH-004":
            (workspace / "repair_loop_temp.py").write_text(
                "def divide(a, b):\n    return a / b\n\nif __name__ == '__main__':\n    print(divide(10, 0))\n",
                encoding="utf-8")
        initial_snapshot = _snapshot(workspace)
        agent = ReActAgent(max_steps=5, workspace=workspace,
                           llm_client=_RepeatErrorLLM() if task["id"] == "BENCH-004"
                           else LLMClient(mode="mock"),
                           state_dir=Path(temp) / "task_state")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            agent.run(task["description"], SCENARIOS[task["id"]][0])
        state = agent.task_state.state
        result = state.get("latest_execution_result") or {}
        classification = state.get("error_classification")
        final_snapshot = _snapshot(workspace)
        agent_history = agent.history
        scenario_output = result.get("output", "")
        errors = [
            str(item.get("result", {}).get("error", ""))
            for item in agent_history if item.get("phase") == "OBSERVE"
        ]
        checks = {"docker_execution": result.get("execution_mode") == "docker-sandbox"}
        if task["id"] == "BENCH-001":
            checks.update({
                "agent_success": state.get("status") == "SUCCESS",
                "zero_exit": result.get("return_code") == 0 and result.get("success") is True,
                "expected_output": "120" in scenario_output,
            })
        elif task["id"] == "BENCH-002":
            checks.update({
                "division_error_observed": any("ZeroDivisionError" in error for error in errors),
                "repair_recorded": "repair_loop_temp.py" in state.get("files_changed", []),
                "source_changed": _changed_from_checkpoint(initial_snapshot, final_snapshot, "repair_loop_temp.py"),
                "reexecution_succeeded": state.get("status") == "SUCCESS" and result.get("success") is True and result.get("return_code") == 0,
                "expected_output": "Cannot divide by zero" in scenario_output,
            })
        elif task["id"] == "BENCH-003":
            checks.update({
                "unknown_name_error_observed": any("NameError" in error for error in errors),
                "agent_refused_repair": any(item.get("phase") == "REFLECT" and item.get("decision") == "STOP" for item in agent_history),
                "working_state_matches_checkpoint": initial_snapshot == final_snapshot,
                "agent_rollback_verified": state.get("status") == "ROLLED_BACK" and state.get("rollback_result", {}).get("verified") is True,
            })
        else:
            checks.update({
                "repeated_error_detected": classification == "REPEATED_ERROR" and len(state.get("previous_error_signatures", [])) >= 2,
                "bounded_stop": state.get("iterations") == 2 and state.get("status") == "ROLLED_BACK",
                "agent_rollback_verified": state.get("rollback_result", {}).get("verified") is True,
                "working_state_matches_checkpoint": initial_snapshot == final_snapshot,
            })
        if classification == "INFRA_ERROR":
            status = "BLOCKED_INFRASTRUCTURE"
        elif classification == "TIMEOUT" or time.monotonic() - started > timeout:
            status = "TIMED_OUT"
        else:
            status = "PASSED" if all(checks.values()) else "FAILED"
        record = {
            "id": task["id"], "name": task["name"],
            "scenario_purpose": SCENARIO_PURPOSE[task["id"]], "status": status,
            "final_agent_status": state.get("status"),
            "iterations": state.get("iterations", 0),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "execution_result": result,
            "error_classification": classification,
            "acceptance_check": state.get("final_summary"),
            "acceptance_checks": checks,
            "working_state_matches_checkpoint": initial_snapshot == final_snapshot,
            "llm_mode": "mock",
            "mock_mode_note": "Deterministic demonstration; not evidence of live model capability.",
        }
    record["temporary_workspace_discarded"] = not workspace.exists()
    if task["id"] in {"BENCH-003", "BENCH-004"}:
        record["rollback_evidence"] = record.get("acceptance_check", {}).get("rollback_result")
        record["temporary_workspace_note"] = "Benchmark copy was removed after the agent's scoped rollback result was verified."
        if not record["temporary_workspace_discarded"]:
            record["status"] = "FAILED"
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if record["elapsed_seconds"] > timeout and record["status"] != "BLOCKED_INFRASTRUCTURE":
        record["status"] = "TIMED_OUT"
    return record


def _snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def _changed_from_checkpoint(before, after, relative_path):
    return before.get(relative_path) != after.get(relative_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", action="append", dest="scenarios",
                        help="Scenario ID; repeat to select multiple (default: all).")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path (default: stdout).")
    args = parser.parse_args(argv)
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    selected = args.scenarios or list(SCENARIOS)
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        parser.error("unknown scenario ID(s): " + ", ".join(unknown))
    results = []
    source_workspace = ROOT / "workspace"
    for task in suite["tasks"]:
        if task["id"] in selected:
            results.append(run_one(task, source_workspace))
    report = {"suite": suite["benchmark_suite"], "results": results,
              "passed": sum(r["status"] == "PASSED" for r in results),
              "failed": sum(r["status"] == "FAILED" for r in results),
              "blocked": sum(r["status"] == "BLOCKED_INFRASTRUCTURE" for r in results),
              "timed_out": sum(r["status"] == "TIMED_OUT" for r in results)}
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return int(report["failed"] > 0 or report["blocked"] > 0 or report["timed_out"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
