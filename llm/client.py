import json
import os
import boto3


class LLMClient:
    def __init__(self, mode=None):
        self.mode = mode or os.getenv("CODEPILOT_MODE", "mock")
        self.region = os.getenv("AWS_REGION", "ap-southeast-2")
        self.model_id = os.getenv(
            "BEDROCK_MODEL_ID",
            "amazon.nova-micro-v1:0"
        )

        self.client = None

        if self.mode == "bedrock":
            self.client = boto3.client(
                "bedrock-runtime",
                region_name=self.region
            )

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

    # ---------------------------------------------------------
    # BEDROCK
    # ---------------------------------------------------------

    def _call_bedrock(self, prompt):
        response = self.client.converse(
            modelId=self.model_id,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            inferenceConfig={
                "maxTokens": 500,
                "temperature": 0
            }
        )

        return response["output"]["message"]["content"][0]["text"]

    def _extract_json(self, text):
        text = text.strip()

        if text.startswith("```"):
            lines = text.splitlines()

            if lines[0].startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            text = "\n".join(lines)

        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1:
            raise ValueError(
                f"Bedrock did not return valid JSON:\n{text}"
            )

        return json.loads(text[start:end + 1])

    def _bedrock_plan(self, task):
        prompt = f"""
You are the planning engine of CodePilot-AI,
an autonomous coding agent.

Task:
{task}

Create a safe step-by-step plan for solving this coding task.

Return ONLY valid JSON in this exact format:

{{
  "task": "{task}",
  "subtasks": [
    "step 1",
    "step 2",
    "step 3"
  ]
}}

Keep the plan concise.
Do not include markdown.
"""

        response = self._call_bedrock(prompt)
        return self._extract_json(response)

    def _bedrock_error_analysis(self, error, code):
        prompt = f"""
You are the error-analysis engine of CodePilot-AI.

Analyze the following Python error.

ERROR:
{error}

CODE:
{code}

Decide whether the error can be safely corrected automatically.

Return ONLY valid JSON in this exact format:

{{
  "action": "fix",
  "reason": "short explanation",
  "confidence": 0.0
}}

The action must be either:
"fix"
or
"stop"

Use "stop" when automatic correction is uncertain or unsafe.

Do not include markdown.
"""

        response = self._call_bedrock(prompt)
        return self._extract_json(response)

    # ---------------------------------------------------------
    # MOCK MODE
    # ---------------------------------------------------------

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


if __name__ == "__main__":
    print("CODEPILOT LLM CLIENT")
    print("=" * 50)

    print("Mode:", os.getenv("CODEPILOT_MODE", "mock"))
    print("Region:", os.getenv("AWS_REGION", "ap-southeast-2"))
    print(
        "Model:",
        os.getenv(
            "BEDROCK_MODEL_ID",
            "amazon.nova-micro-v1:0"
        )
    )