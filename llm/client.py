import os


class LLMClient:
    def __init__(self, mode=None):
        self.mode = mode or os.getenv("CODEPILOT_MODE", "mock")

    def generate_plan(self, task):
        if self.mode == "mock":
            return self._mock_plan(task)

        if self.mode == "bedrock":
            return self._bedrock_plan(task)

        raise ValueError(f"Unsupported LLM mode: {self.mode}")

    def analyze_error(self, error, code):
        if self.mode == "mock":
            return self._mock_error_analysis(error, code)

        if self.mode == "bedrock":
            return self._bedrock_error_analysis(error, code)

        raise ValueError(f"Unsupported LLM mode: {self.mode}")

    def _mock_plan(self, task):
        return {
            "task": task,
            "subtasks": [
                "Inspect the target code",
                "Identify the relevant function or module",
                "Run the current implementation",
                "Analyze the execution result",
                "Apply a safe correction if required",
                "Run the implementation again",
                "Validate the final result"
            ]
        }

    def _mock_error_analysis(self, error, code):
        if "ZeroDivisionError" in error:
            return {
                "action": "fix",
                "reason": "Division by zero detected",
                "confidence": 1.0
            }

        return {
            "action": "stop",
            "reason": "No safe automatic correction available",
            "confidence": 1.0
        }

    def _bedrock_plan(self, task):
        raise NotImplementedError(
            "AWS Bedrock mode will be added after the local agent is complete."
        )

    def _bedrock_error_analysis(self, error, code):
        raise NotImplementedError(
            "AWS Bedrock mode will be added after the local agent is complete."
        )


if __name__ == "__main__":
    client = LLMClient()

    print("CODEPILOT LLM CLIENT")
    print("-" * 40)
    print(f"Mode: {client.mode}")

    plan = client.generate_plan("Fix the buggy program")

    print("\nGenerated plan:")
    for number, step in enumerate(plan["subtasks"], 1):
        print(f"{number}. {step}")

    analysis = client.analyze_error(
        "ZeroDivisionError: division by zero",
        "return a / b"
    )

    print("\nError analysis:")
    print(analysis)
