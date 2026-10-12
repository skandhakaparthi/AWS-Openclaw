from tools.filesystem import list_files, read_file, write_file
from tools.executor import run_python_file


class CodingAgent:

    def __init__(self):
        self.step = 0
        self.max_steps = 5

    def show_workspace(self):

        print("\n📁 WORKSPACE")
        print("-" * 40)

        files = list_files()

        if not files:
            print("No files found.")
        else:
            for file in files:
                print(file)

    def read_code(self, filename):

        print(f"\n📖 Reading: {filename}")

        content = read_file(filename)

        print("-" * 40)
        print(content)
        print("-" * 40)

        return content

    def write_code(self, filename, content):

        print(f"\n✍️ Writing: {filename}")

        result = write_file(filename, content)

        print(result)

        return result

    def run_code(self, filename):

        print(f"\n▶️ Running: {filename}")

        result = run_python_file(filename)

        if result["success"]:

            print("✅ Execution successful")

            if result["output"]:
                print("\nOUTPUT:")
                print(result["output"])

        else:

            print("❌ Execution failed")

            print("\nERROR:")
            print(result["error"])

        return result

    def fix_division_error(self, filename):

        print("\n🧠 ANALYZING ERROR...")

        code = self.read_code(filename)

        if "return a / b" in code:

            print("\n🔧 Agent found a division-by-zero problem.")

            fixed_code = """def divide(a, b):
    if b == 0:
        return "Cannot divide by zero"
    return a / b


if __name__ == "__main__":
    print(divide(10, 0))
"""

            self.write_code(filename, fixed_code)

            print("\n✅ Fix applied.")

            return True

        return False

    def run_with_self_correction(self, filename):

        for step in range(1, self.max_steps + 1):

            self.step = step

            print("\n" + "=" * 50)
            print(f"🔄 AGENT STEP {step}")
            print("=" * 50)

            result = self.run_code(filename)

            if result["success"]:

                print("\n🎉 TASK COMPLETED!")

                return True

            print("\n⚠️ Error detected.")

            fixed = self.fix_division_error(filename)

            if not fixed:

                print("\n❌ Agent could not automatically fix this error.")

                return False

            print("\n🔁 Retrying...")

        print("\n🛑 Maximum number of steps reached.")

        return False

    def run(self, task):

        print("\n" + "=" * 50)
        print("🤖 CODEPILOT AI")
        print("=" * 50)

        print("\nTASK: content omitted from logs")

        self.show_workspace()

        if "fix" in task.lower() and "buggy" in task.lower():

            print("\n📋 PLAN")

            print("1. Read buggy.py")
            print("2. Run the program")
            print("3. Analyze errors")
            print("4. Fix the code")
            print("5. Run the program again")
            print("6. Stop when successful")

            self.run_with_self_correction("buggy.py")

        else:

            print("\n⚠️ Current demo supports:")

            print("Fix the buggy program")


if __name__ == "__main__":

    agent = CodingAgent()

    task = input("\nEnter your coding task: ")

    agent.run(task)
