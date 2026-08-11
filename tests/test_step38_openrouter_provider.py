"""Step 38 OpenRouter provider decision and authority-boundary tests."""

from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from aioa_memory_kernel.modeling import (
    DraftV1Service,
    ModelAdapterError,
    ModelReasonCode,
    build_provider_call_request,
    load_approved_provider_spec,
)
from aioa_memory_kernel.modeling.providers import OpenRouterDraftV1Adapter
from aioa_memory_kernel.modeling.providers import openrouter
from aioa_memory_kernel.security.credentials import CredentialPurpose, SecretValue
from tests.test_step22_model_adapter_draft_v1 import ORIGINAL_QUERY, generation_request


class OpenRouterProviderDecisionTests(unittest.TestCase):
    def test_exact_provider_model_endpoint_and_credential_are_frozen(self) -> None:
        spec = load_approved_provider_spec()
        self.assertEqual(spec.provider_id, "openrouter")
        self.assertEqual(spec.model_id, "moonshotai/kimi-k2")
        self.assertEqual(spec.model_declared_version, "moonshotai/kimi-k2")
        self.assertEqual(spec.api_origin, "https://openrouter.ai")
        self.assertEqual(spec.chat_completions_path, "/api/v1/chat/completions")
        self.assertEqual(spec.credential_environment_variable, "OPENROUTER_API_KEY")
        self.assertTrue(spec.tooling_disabled)
        self.assertTrue(spec.function_calling_disabled)
        self.assertTrue(spec.web_browsing_disabled)
        self.assertTrue(spec.code_execution_disabled)
        self.assertFalse(spec.immutable_model_revision)

    def test_request_is_text_only_bounded_and_does_not_put_secret_in_body(self) -> None:
        captured = {}
        request = generation_request()

        def transport(endpoint, headers, body, timeout):
            captured.update(endpoint=endpoint, headers=headers, body=body, timeout=timeout)
            return {
                "id": "gen-test",
                "model": "moonshotai/kimi-k2",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Antwort"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            }

        adapter = OpenRouterDraftV1Adapter("secret-test-key", transport=transport)
        response = adapter.generate(
            build_provider_call_request(request), request.timeout_policy
        )
        payload = json.loads(captured["body"])
        self.assertEqual(
            captured["endpoint"],
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(payload["model"], "moonshotai/kimi-k2")
        self.assertEqual(payload["messages"][1]["content"], ORIGINAL_QUERY)
        self.assertFalse(payload["stream"])
        self.assertFalse(
            {"tools", "functions", "tool_choice", "web", "plugins"} & set(payload)
        )
        self.assertNotIn("secret-test-key", captured["body"].decode("utf-8"))
        self.assertEqual(response.response_content, "Antwort")

    def test_from_environment_uses_only_current_inventory_name(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MOONSHOT_API_KEY": "legacy-only-sentinel"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                ModelAdapterError, "MODEL_ADAPTER_UNAVAILABLE"
            ):
                OpenRouterDraftV1Adapter.from_environment()
        with mock.patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "step38-fake-openrouter-sentinel"},
            clear=True,
        ):
            adapter = OpenRouterDraftV1Adapter.from_environment()
        self.assertNotIn("step38-fake-openrouter-sentinel", repr(adapter))
        self.assertIn("redacted", repr(adapter))

    def test_wrong_purpose_secret_is_rejected_without_disclosure(self) -> None:
        raw = "step38-fake-database-sentinel"
        secret = SecretValue(
            raw,
            purpose=CredentialPurpose.APPLICATION_DATABASE,
            source_name="DATABASE_URL_APP",
        )
        with self.assertRaises(ModelAdapterError) as caught:
            OpenRouterDraftV1Adapter(secret)
        self.assertIs(
            caught.exception.reason_code,
            ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
        )
        self.assertNotIn(raw, str(caught.exception))

    def test_response_cannot_change_model_or_invoke_tools(self) -> None:
        request = generation_request()
        call = build_provider_call_request(request)
        cases = (
            (
                {
                    "model": "openrouter/auto",
                    "choices": [
                        {"message": {"content": "x"}, "finish_reason": "stop"}
                    ],
                },
                ModelReasonCode.MODEL_IDENTITY_MISMATCH,
            ),
            (
                {
                    "model": "moonshotai/kimi-k2",
                    "choices": [
                        {
                            "message": {
                                "content": "x",
                                "tool_calls": [{"id": "forbidden"}],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
                ModelReasonCode.MODEL_TOOLING_BOUNDARY_VIOLATION,
            ),
        )
        for decoded, reason in cases:
            with self.subTest(reason=reason.value):
                adapter = OpenRouterDraftV1Adapter(
                    "secret",
                    transport=lambda *_args, decoded=decoded: decoded,
                )
                with self.assertRaises(ModelAdapterError) as caught:
                    adapter.generate(call, request.timeout_policy)
                self.assertIs(caught.exception.reason_code, reason)

    def test_empty_completion_is_retried_once_within_existing_policy(self) -> None:
        request = generation_request()
        calls = 0

        def transport(*_args):
            nonlocal calls
            calls += 1
            content = "" if calls == 1 else "Antwort"
            return {
                "id": f"gen-empty-retry-{calls}",
                "model": "moonshotai/kimi-k2",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 0 if not content else 1,
                    "total_tokens": 4 if not content else 5,
                },
            }

        receipt = DraftV1Service(
            OpenRouterDraftV1Adapter("secret", transport=transport),
            sleep=lambda _delay: None,
        ).generate(request)
        self.assertEqual(calls, 2)
        self.assertEqual(receipt.draft.draft_text, "Antwort")
        self.assertEqual(
            receipt.generation_result.failed_attempt_reason_codes,
            (ModelReasonCode.MODEL_RESPONSE_EMPTY,),
        )

    def test_provider_error_payload_is_never_disclosed(self) -> None:
        request = generation_request()
        sentinel = "provider-private-error-sentinel"
        adapter = OpenRouterDraftV1Adapter(
            "secret",
            transport=lambda *_: {"error": {"message": sentinel}},
        )
        with self.assertRaises(ModelAdapterError) as caught:
            adapter.generate(build_provider_call_request(request), request.timeout_policy)
        self.assertIs(caught.exception.reason_code, ModelReasonCode.MODEL_POLICY_REJECTED)
        self.assertNotIn(sentinel, str(caught.exception))

    def test_http_429_is_bounded_retryable_unknown_completion(self) -> None:
        sentinel = b"provider-private-response-body"
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "rate limited",
            {},
            io.BytesIO(sentinel),
        )
        with mock.patch.object(openrouter.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ModelAdapterError) as caught:
                openrouter._default_transport(
                    "https://openrouter.ai/api/v1/chat/completions",
                    {"Authorization": "Bearer fake"},
                    b"{}",
                    1,
                )
        self.assertIs(caught.exception.reason_code, ModelReasonCode.MODEL_TRANSIENT_FAILURE)
        self.assertTrue(caught.exception.retryable)
        self.assertTrue(caught.exception.unknown_completion)
        self.assertNotIn(sentinel.decode("ascii"), str(caught.exception))

    def test_http_402_fails_without_retry(self) -> None:
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            402,
            "payment required",
            {},
            io.BytesIO(b"private"),
        )
        with mock.patch.object(openrouter.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(ModelAdapterError) as caught:
                openrouter._default_transport(
                    "https://openrouter.ai/api/v1/chat/completions",
                    {"Authorization": "Bearer fake"},
                    b"{}",
                    1,
                )
        self.assertIs(
            caught.exception.reason_code,
            ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE,
        )
        self.assertFalse(caught.exception.retryable)
        self.assertFalse(caught.exception.unknown_completion)


if __name__ == "__main__":
    unittest.main()
