"""Execute the benchmark suite through ReActAgent and its sandboxed tools."""
import argparse
import contextlib
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
        agent = ReActAgent(max_steps=5, workspace=workspace,
                           llm_client=_RepeatErrorLLM() if task["id"] == "BENCH-004"
                           else LLMClient(mode="mock"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            summary = agent.run(task["description"], SCENARIOS[task["id"]][0])
        state = agent.task_state.state
        result = state.get("latest_execution_result") or {}
        classification = state.get("error_classification")
        if classification == "INFRA_ERROR":
            status = "BLOCKED_INFRASTRUCTURE"
        elif classification == "TIMEOUT" or time.monotonic() - started > timeout:
            status = "TIMED_OUT"
        elif task["id"] == "BENCH-003" and state.get("status") == "FAILED" and classification == "RUNTIME_ERROR":
            status = "PASSED"
        elif task["id"] == "BENCH-004" and classification == "REPEATED_ERROR":
            status = "PASSED"
        elif state.get("status") == SCENARIOS[task["id"]][1]:
            status = "PASSED"
        else:
            status = "FAILED"
        # A repair scenario passes only if source changed and the final run passed.
        if task["id"] == "BENCH-002" and status != "BLOCKED_INFRASTRUCTURE":
            original = (source_workspace / "repair_loop_temp.py").read_text(encoding="utf-8")
            repaired = (workspace / "repair_loop_temp.py").read_text(encoding="utf-8")
            if original == repaired or not result.get("success"):
                status = "FAILED"
        return {
            "id": task["id"], "name": task["name"],
            "scenario_purpose": SCENARIO_PURPOSE[task["id"]], "status": status,
            "final_agent_status": state.get("status"),
            "iterations": state.get("iterations", 0),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "execution_result": result,
            "error_classification": classification,
            "acceptance_check": state.get("final_summary"),
            "llm_mode": "mock",
            "mock_mode_note": "Deterministic demonstration; not evidence of live model capability.",
        }


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
