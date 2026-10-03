import json
from pathlib import Path
from datetime import datetime


class TaskState:
    def __init__(self, task_id):
        self.task_id = task_id
        self.state = {
            "task_id": task_id,
            "status": "CREATED",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "iterations": 0,
            "estimated_cost": 0.0,
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

        if status in ["SUCCESS", "FAILED", "ROLLED_BACK"]:
            self.state["completed_at"] = datetime.now().isoformat()

    def add_cost(self, amount):
        self.state["estimated_cost"] += amount

    def save(self):
        Path("task_state").mkdir(exist_ok=True)

        filename = Path("task_state") / f"{self.task_id}.json"

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
