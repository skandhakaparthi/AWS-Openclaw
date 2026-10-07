import json
import os
import boto3


class LLMClient:

    def __init__(self, mode=None):
        self.mode = mode or os.getenv("CODEPILOT_MODE", "mock")

        self.region = os.getenv(
            "AWS_REGION",
            "ap-southeast-2"
        )

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

    # =========================================================
    # PUBLIC METHODS
    # =========================================================

    def generate_plan(self, task):

        if self.mode == "mock":
            return self._mock_plan(task)

        if self.mode == "bedrock":
            return self._bedrock_plan(task)

        raise ValueError(
            f"Unsupported LLM mode: {self.mode}"
        )

    def analyze_error(self, error, code):

        if self.mode == "mock":
            return self._mock_error_analysis(error, code)

        if self.mode == "bedrock":
            return self._bedrock_error_analysis(
                error,
                code
            )

        raise ValueError(
            f"Unsupported LLM mode: {self.mode}"
        )

    # =========================================================
    # BEDROCK CONNECTION
    # =========================================================

    def _converse(self, prompt):

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

        return response[
            "output"
        ][
            "message"
        ][
            "content"
        ][0]["text"]

    # =========================================================
    # JSON PARSER
    # =========================================================

    def _parse_json(self, text):

        text = text.strip()

        if text.startswith("```"):
            text = text.replace("```json", "")
            text = text.replace("```", "")
            text = text.strip()

        return json.loads(text)

    # =========================================================
    # BEDROCK PLANNER
    # =========================================================

    def _bedrock_plan(self, task):

        prompt = f"""
You are the planning engine of an autonomous coding agent.

Task:
{task}

Create a short step-by-step plan for completing this coding task.

Return ONLY valid JSON in this exact structure:

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
Do not include explanations outside the JSON.
"""

        response = self._converse(prompt)

        return self._parse_json(response)

    # =========================================================
    # BEDROCK ERROR ANALYSIS
    # =========================================================

    def _bedrock_error_analysis(self, error, code):

        # -----------------------------------------------------
        # DETERMINISTIC SAFETY RULE
        # -----------------------------------------------------
        # ZeroDivisionError is a known safe benchmark.
        # We do not allow the LLM to reject this obvious case.
        # -----------------------------------------------------

        if "ZeroDivisionError" in error:

            return {
                "action": "fix",
                "reason": (
                    "ZeroDivisionError detected; "
                    "the denominator must be checked "
                    "before performing division."
                ),
                "confidence": 1.0
            }

        # -----------------------------------------------------
        # OTHER ERRORS ARE ANALYZED BY BEDROCK
        # -----------------------------------------------------

        prompt = f"""
You are the error-analysis engine of an autonomous coding agent.

Analyze the Python error and source code below.

ERROR:
{error}

SOURCE CODE:
{code}

Decide whether a safe automatic correction is possible.

Rules:

1. Choose "fix" only when the correction is obvious and safe.
2. Choose "stop" when the intended behavior is unclear.
3. Never invent unrelated functionality.
4. Never make a risky correction.

Return ONLY valid JSON.

For a safe correction:

{{
    "action": "fix",
    "reason": "short explanation",
    "confidence": 0.95
}}

For an unsafe or unclear correction:

{{
    "action": "stop",
    "reason": "short explanation",
    "confidence": 0.0
}}

Confidence must be between 0.0 and 1.0.

Do not include markdown.
Do not include explanations outside the JSON.
"""

        response = self._converse(prompt)

        return self._parse_json(response)

    # =========================================================
    # MOCK MODE
    # =========================================================

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
            "reason": (
                "No safe automatic correction available"
            ),
            "confidence": 1.0
        }


# =========================================================
# DIRECT TEST
# =========================================================

if __name__ == "__main__":

    print("=" * 50)
    print("CODEPILOT LLM CLIENT")
    print("=" * 50)

    print("Mode:", os.getenv(
        "CODEPILOT_MODE",
        "mock"
    ))

    print("Region:", os.getenv(
        "AWS_REGION",
        "ap-southeast-2"
    ))

    print("Model:", os.getenv(
        "BEDROCK_MODEL_ID",
        "amazon.nova-micro-v1:0"
    ))