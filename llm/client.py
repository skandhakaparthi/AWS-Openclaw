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

    def generate_fix(self, error, code):
        if self.mode == "mock":
            return self._mock_generate_fix(error, code)

        if self.mode == "bedrock":
            return self._bedrock_generate_fix(error, code)

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
                "maxTokens": 2000,
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

    def _bedrock_generate_fix(self, error, code):
        prompt = f"""
You are the code-repair engine of CodePilot-AI.

Repair the Python program using the reported error.

ERROR:
{error}

CURRENT SOURCE CODE:
{code}

Requirements:
- Return the complete corrected Python source code only.
- Preserve the intended behavior.
- Make the smallest safe correction possible.
- Do not add unrelated features or Markdown.
- If a safe correction cannot be determined, return exactly:
CANNOT_FIX
"""
        response = self._call_bedrock(prompt).strip()

        if response.startswith("```"):
            lines = response.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response = "\n".join(lines).strip()

        if response == "CANNOT_FIX" or not response:
            return None

        return response

    def _mock_generate_fix(self, error, code):
        if "zerodivisionerror" not in error.lower():
            return None

        if "return a / b" not in code:
            return None

        fixed_code = code.replace(
            "return a / b",
            'if b == 0:\n        return "Cannot divide by zero"\n    return a / b',
            1
        )

        return fixed_code if fixed_code != code else None

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
        error_text = error.lower()
        code_text = code.lower()

        if "zerodivisionerror" in error_text:
            return {
                "action": "fix",
                "reason": "Division by zero detected",
                "confidence": 1.0
            }

        if "nameerror" in error_text:
            return {
                "action": "stop",
                "reason": (
                    "Undefined name detected. "
                    "Automatic correction requires identifying "
                    "the intended variable or import."
                ),
                "confidence": 0.95
            }

        if "syntaxerror" in error_text:
            return {
                "action": "stop",
                "reason": (
                    "Syntax error detected. "
                    "The source needs inspection before correction."
                ),
                "confidence": 0.95
            }

        if "typeerror" in error_text:
            return {
                "action": "stop",
                "reason": (
                    "Type error detected. "
                    "The expected types and intended behavior "
                    "must be established before editing."
                ),
                "confidence": 0.95
            }

        return {
            "action": "stop",
            "reason": (
                "Unrecognized or insufficiently understood error. "
                "Stopping to avoid an unsafe automatic correction."
            ),
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