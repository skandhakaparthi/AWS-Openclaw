import json
import os
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

from llm.client import (
    LLMAuthenticationError,
    LLMClient,
    LLMConfigurationError,
    LLMModelAccessError,
    LLMResponseError,
    LLMThrottlingError,
    LLMTransientError,
)


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def response(text, usage=None, latency=None):
    result = {"output": {"message": {"content": [{"text": text}]}}}
    if usage is not None:
        result["usage"] = usage
    if latency is not None:
        result["metrics"] = {"latencyMs": latency}
    return result


class TestLLMClient(unittest.TestCase):
    def bedrock_client(self, fake, **extra):
        env = {
            "CODEPILOT_MODE": "bedrock",
            "CODEPILOT_ALLOW_UNMETERED_BEDROCK": "true",
            "AWS_REGION": "ap-southeast-2",
            "BEDROCK_MODEL_ID": "amazon.nova-micro-v1:0",
        }
        env.update(extra)
        with patch.dict(os.environ, env, clear=False):
            return LLMClient(bedrock_client=fake)

    def test_mock_mode_is_default_deterministic_and_needs_no_aws(self):
        with patch.dict(os.environ, {"CODEPILOT_MODE": "mock"}, clear=False), \
                patch("llm.client.boto3.Session") as session:
            client = LLMClient()
            self.assertEqual(client.mode, "mock")
            self.assertEqual(client.generate_plan("task"), client.generate_plan("task"))
            session.assert_not_called()

    def test_bedrock_mode_selects_configured_model_and_records_usage(self):
        fake = FakeBedrock(response('{"subtasks":["inspect"]}',
                                    {"inputTokens": 23, "outputTokens": 7}, 42))
        client = self.bedrock_client(fake)
        result = client.generate_plan("task")
        self.assertEqual(result["subtasks"], ["inspect"])
        self.assertEqual(fake.calls[0]["modelId"], "amazon.nova-micro-v1:0")
        self.assertEqual(fake.calls[0]["inferenceConfig"]["maxTokens"], 2048)
        self.assertEqual(client.usage_summary()["request_count"], 1)
        self.assertEqual(client.usage_summary()["input_tokens"], 23)
        self.assertEqual(client.usage_summary()["output_tokens"], 7)
        self.assertGreaterEqual(len(client.usage_summary()["latency_ms"]), 1)
        self.assertEqual(client.usage_summary()["provider_latency_ms"], [42])
        self.assertIsNone(client.usage_summary()["estimated_cost_usd"])
        self.assertFalse(client.usage_summary()["cost_limit_enforced"])

    def test_unmetered_bedrock_is_blocked_without_explicit_configuration(self):
        with patch.dict(os.environ, {"CODEPILOT_MODE": "bedrock"}, clear=False):
            os.environ.pop("CODEPILOT_ALLOW_UNMETERED_BEDROCK", None)
            with self.assertRaisesRegex(LLMConfigurationError, "cost budget"):
                LLMClient(bedrock_client=FakeBedrock())

    def test_missing_credentials_are_a_provider_authentication_error(self):
        fake = FakeBedrock(error=NoCredentialsError())
        client = self.bedrock_client(fake)
        with self.assertRaises(LLMAuthenticationError):
            client.generate_plan("task")
        self.assertEqual(client.usage_summary()["provider_errors"], ["authentication_error"])

    def test_authentication_failure_does_not_expose_provider_message(self):
        secret_text = "do-not-log-this-prompt-or-token"
        error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": secret_text}}, "Converse")
        client = self.bedrock_client(FakeBedrock(error=error))
        with self.assertRaises(LLMAuthenticationError) as caught:
            client.generate_plan(secret_text)
        self.assertNotIn(secret_text, str(caught.exception))

    def test_throttling_and_transient_errors_are_distinguished(self):
        throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse")
        with self.assertRaises(LLMThrottlingError):
            self.bedrock_client(FakeBedrock(error=throttle)).generate_plan("task")
        with self.assertRaises(LLMTransientError):
            self.bedrock_client(FakeBedrock(error=ReadTimeoutError(endpoint_url="https://example.invalid"))).generate_plan("task")

    def test_sdk_retry_configuration_is_bounded(self):
        session = Mock()
        session.client.return_value = FakeBedrock(response=response('{"subtasks":["ok"]}'))
        with patch.dict(os.environ, {
            "CODEPILOT_MODE": "bedrock",
            "CODEPILOT_ALLOW_UNMETERED_BEDROCK": "true",
        }, clear=False), patch("llm.client.boto3.Session", return_value=session):
            LLMClient()
        config = session.client.call_args.kwargs["config"]
        self.assertEqual(config.retries["mode"], "standard")
        self.assertEqual(config.retries["total_max_attempts"], 2)

    def test_explicit_aws_profile_uses_boto_provider_chain(self):
        session = Mock()
        session.client.return_value = FakeBedrock(response('{"subtasks":["ok"]}'))
        with patch.dict(os.environ, {
            "CODEPILOT_MODE": "bedrock",
            "CODEPILOT_ALLOW_UNMETERED_BEDROCK": "true",
            "BEDROCK_AWS_PROFILE": "developer-profile",
        }, clear=False), patch("llm.client.boto3.Session", return_value=session) as session_factory:
            LLMClient()
        session.client.assert_called_once()
        session_factory.assert_called_once_with(profile_name="developer-profile")

    def test_model_access_error_is_distinct(self):
        error = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}}, "Converse")
        with self.assertRaises(LLMModelAccessError):
            self.bedrock_client(FakeBedrock(error=error)).generate_plan("task")

    def test_malformed_json_and_schema_are_rejected(self):
        with self.assertRaises(LLMResponseError):
            self.bedrock_client(FakeBedrock(response("not json"))).generate_plan("task")
        with self.assertRaises(LLMResponseError):
            self.bedrock_client(FakeBedrock(response('{"subtasks":[]}'))).generate_plan("task")

    def test_unknown_usage_and_cost_are_not_fabricated_as_zero(self):
        client = self.bedrock_client(FakeBedrock(response('{"subtasks":["ok"]}')))
        client.generate_plan("task")
        usage = client.usage_summary()
        self.assertFalse(usage["token_usage_available"])
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertIsNone(usage["estimated_cost_usd"])
        self.assertIsNone(usage["actual_charges_usd"])

    def test_prompt_size_limit_and_validation(self):
        fake = FakeBedrock(response('{"subtasks":["ok"]}'))
        client = self.bedrock_client(fake, BEDROCK_MAX_PROMPT_CHARS="1000")
        with self.assertRaises(LLMConfigurationError):
            client.generate_plan("x" * 2000)
        self.assertEqual(fake.calls, [])
        with patch.dict(os.environ, {"CODEPILOT_MODE": "bedrock", "CODEPILOT_ALLOW_UNMETERED_BEDROCK": "true",
                                     "BEDROCK_MAX_TOKENS": "-1"}, clear=False):
            with self.assertRaises(LLMConfigurationError):
                LLMClient(bedrock_client=FakeBedrock())


if __name__ == "__main__":
    unittest.main()
