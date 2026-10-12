import json
from pathlib import Path
from datetime import datetime


class TaskState:
    def __init__(self, task_id, original_request=None, state_dir=None):
        self.task_id = task_id
        self.state_dir = Path(state_dir) if state_dir is not None else Path("task_state")
        self.state = {
            "task_id": task_id,
            "status": "CREATED",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "iterations": 0,
            "estimated_cost": None,
            "cost_measurement_available": False,
            "original_request": original_request,
            "plan": [],
            "current_step": 0,
            "files_inspected": [],
            "files_changed": [],
            "tool_invocations": [],
            "latest_execution_result": None,
            "latest_test_result": None,
            "error_classification": None,
            "previous_error_signatures": [],
            "final_summary": None,
            "rollback_occurred": False,
            "history": []
        }

    def record_iteration(self, iteration, phase, details):
        self.state["iterations"] = iteration

        self.state["history"].append({
            "iteration": iteration,
            "phase": phase,
            "details": details,
            "timestamp": datetime.now().isoformat()
        })

    def set_status(self, status):
        self.state["status"] = status

        if status in ["SUCCESS", "FAILED", "ROLLED_BACK", "CANCELLED", "CLEANUP_FAILED"]:
            self.state["completed_at"] = datetime.now().isoformat()

    def add_cost(self, amount):
        current = self.state["estimated_cost"] or 0.0
        self.state["estimated_cost"] = current + amount

    def save(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        filename = self.state_dir / f"{self.task_id}.json"

        filename.write_text(
            json.dumps(self.state, indent=2),
            encoding="utf-8"
        )

        return str(filename)

    def show(self):
        print("\nTASK STATE")
        print("-" * 50)
        print(json.dumps(self.state, indent=2))


if __name__ == "__main__":
    state = TaskState("demo-task-001")

    state.record_iteration(
        1,
        "OBSERVE",
        "Program execution completed successfully."
    )

    state.add_cost(0.00)
    state.set_status("SUCCESS")

    print("State saved to:")
    print(state.save())

    state.show()
