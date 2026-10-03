from llm.client import LLMClient


class TaskPlanner:
    def __init__(self, llm_client=None):
        self.llm = llm_client or LLMClient()

    def create_plan(self, task):
        print("\nTASK DECOMPOSITION")
        print("-" * 50)

        result = self.llm.generate_plan(task)
        subtasks = result["subtasks"]

        print(f"Task: {task}\n")

        for number, subtask in enumerate(subtasks, 1):
            print(f"Subtask {number}: {subtask}")

        return subtasks


if __name__ == "__main__":
    planner = TaskPlanner()

    planner.create_plan(
        "Fix the buggy program"
    )
