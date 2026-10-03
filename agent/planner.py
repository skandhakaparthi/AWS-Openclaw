class TaskPlanner:
    def create_plan(self, task):
        print("\nTASK DECOMPOSITION")
        print("-" * 50)

        subtasks = [
            "Inspect the target code",
            "Identify the relevant function or module",
            "Run the current implementation",
            "Analyze the execution result",
            "Apply a safe correction if required",
            "Run the implementation again",
            "Validate the final result"
        ]

        print(f"Task: {task}\n")

        for number, subtask in enumerate(subtasks, 1):
            print(f"Subtask {number}: {subtask}")

        return subtasks


if __name__ == "__main__":
    planner = TaskPlanner()
    planner.create_plan("Fix the buggy program")
