from tools.filesystem import read_file, write_file
from tools.executor import run_python_file


class ReActAgent:

    def __init__(self, max_steps=5):
        self.max_steps = max_steps
        self.history = []

    def plan(self, task):
        plan = [
            "Inspect the relevant code",
            "Execute the code",
            "Observe the result",
            "Analyze any error",
            "Apply a correction if necessary",
            "Execute again"
        ]

        print("\nPLAN")
        print("-" * 50)

        for number, step in enumerate(plan, 1):
            print(f"{number}. {step}")

        self.history.append({
            "phase": "PLAN",
            "task": task,
            "plan": plan
        })

    def act(self, action):
        print("\nACT")
        print("-" * 50)
        print(action)

        self.history.append({
            "phase": "ACT",
            "action": action
        })

    def observe(self, filename):
        result = run_python_file(filename)

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

        print("Error detected.")
        print(error)

        code = read_file(filename)

        if "ZeroDivisionError" in error or "return a / b" in code:

            print("\nCorrection identified:")
            print("Add protection against division by zero.")

            fixed_code = '''def divide(a, b):
    if b == 0:
        return "Cannot divide by zero"
    return a / b


if __name__ == "__main__":
    print(divide(10, 0))
'''

            write_file(filename, fixed_code)

            print("Fix applied.")

            self.history.append({
                "phase": "REFLECT",
                "decision": "FIX",
                "reason": "Division by zero"
            })

            return "FIX"

        print("No safe automatic correction available.")

        self.history.append({
            "phase": "REFLECT",
            "decision": "STOP"
        })

        return "STOP"

    def run(self, task, filename):

        print("=" * 60)
        print("CODEPILOT AI - ReAct CODING AGENT")
        print("=" * 60)

        print("\nTASK:")
        print(task)

        self.plan(task)

        for step in range(1, self.max_steps + 1):

            print("\n" + "=" * 60)
            print(f"ITERATION {step}")
            print("=" * 60)

            self.act("Execute target program")

            result = self.observe(filename)

            decision = self.reflect(result, filename)

            if decision == "SUCCESS":
                print("\nReAct loop completed successfully.")
                break

            if decision == "STOP":
                print("\nAgent stopped.")
                break

            if decision == "FIX":
                print("\nReAct loop continuing...")

        else:
            print("\nHARD STOP: Maximum iterations reached.")

        print("\n" + "=" * 60)
        print("AGENT HISTORY")
        print("=" * 60)

        for item in self.history:
            print(item)


if __name__ == "__main__":

    agent = ReActAgent(max_steps=5)

    agent.run(
        "Fix the buggy program",
        "buggy.py"
    )
