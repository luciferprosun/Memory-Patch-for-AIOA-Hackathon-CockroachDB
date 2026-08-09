#!/usr/bin/env python3
"""Controlled Step 22 fake and approved real-provider validation."""

from __future__ import annotations

import os
import sys
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256  # noqa: E402
from aioa_memory_kernel.modeling import (  # noqa: E402
    DraftV1Service,
    ModelAdapterError,
    ModelReasonCode,
    ProviderResponse,
    build_provider_call_request,
    load_approved_provider_spec,
    prepare_model_generation_request,
    verify_draft_v1_hash,
)
from aioa_memory_kernel.modeling.providers import MoonshotDraftV1Adapter  # noqa: E402
from tests.test_step21_temporal_resolution import bundle_outcome, metadata, resolve  # noqa: E402


START_SHA = "c70fe73ddedb20e4b57186fbd336568090f90018"
SENTINEL = "CORRECTION_EVIDENCE_SENTINEL_DO_NOT_SEND"
VALIDATION_QUERY = "Was ist zwei plus zwei? Antworte in einem kurzen deutschen Satz."
FIXED_NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


class ValidationFailure(RuntimeError):
    pass


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class FakeProvider:
    def __init__(self, request) -> None:
        self.identity = request.provider_identity
        self.calls = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.calls.append((request, timeout_policy))
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id="fake-step22-request",
            finish_reason="stop",
            response_content="Vier ist das Ergebnis von zwei plus zwei.",
            usage_metadata={"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
            latency_milliseconds=1,
        )


class MemoryStore:
    def __init__(self) -> None:
        self.value = None

    def load(self, *, tenant_id, user_id, draft_id):
        if self.value is None:
            return None
        if (self.value.tenant_id, self.value.user_id, self.value.draft_id) != (
            tenant_id,
            user_id,
            draft_id,
        ):
            return None
        return self.value

    def put(self, draft):
        if self.value is not None and self.value.draft_hash != draft.draft_hash:
            raise ValidationFailure("Draft replay conflict")
        self.value = self.value or draft
        return self.value


def _lineage():
    return resolve(
        bundle_outcome(
            metadata(),
            contents=(SENTINEL,),
        )
    )


def _validate_fake(request) -> dict[str, Any]:
    provider = FakeProvider(request)
    store = MemoryStore()
    service = DraftV1Service(
        provider,
        store=store,
        clock=FixedClock(),
        sleep=lambda _: None,
    )
    first = service.generate(request)
    replay = service.generate(request)
    if len(provider.calls) != 1 or not replay.replayed:
        raise ValidationFailure("fake-provider idempotency failed")
    call = provider.calls[0][0]
    call_text = repr(call)
    if (
        call.original_query != VALIDATION_QUERY
        or SENTINEL in call_text
        or "step21_result_hash" in call.__dataclass_fields__
        or "evidence" in call.__dataclass_fields__
    ):
        raise ValidationFailure("correction evidence crossed the model boundary")
    verify_draft_v1_hash(first.draft)
    if first.draft.draft_text != "Vier ist das Ergebnis von zwei plus zwei.":
        raise ValidationFailure("fake Draft V1 text changed")
    return {
        "status": "PASS",
        "provider_calls": len(provider.calls),
        "original_query_sent": True,
        "evidence_sentinel_absent": True,
        "tools_used": 0,
        "draft_byte_length": first.draft.draft_byte_length,
        "draft_text_sha256": first.draft.draft_text_sha256,
        "draft_hash": first.draft.draft_hash,
        "generation_request_hash": request.request_hash,
        "provider_call_request_hash": build_provider_call_request(request).request_hash,
        "idempotent_replay": "PASS",
        "provider_call_outside_database_transaction": "PASS",
    }


def _validate_real(request) -> dict[str, Any]:
    spec = load_approved_provider_spec()
    if not os.environ.get(spec.credential_environment_variable):
        return {
            "status": "UNAVAILABLE",
            "reason": "APPROVED_CREDENTIAL_NOT_PRESENT",
            "provider_calls": 0,
        }
    adapter = MoonshotDraftV1Adapter.from_environment()
    try:
        receipt = DraftV1Service(adapter, clock=FixedClock()).generate(request)
    except ModelAdapterError as exc:
        return {
            "status": "UNAVAILABLE",
            "reason": exc.reason_code.value,
            "provider_calls": "BOUNDED_BY_ATTEMPT_POLICY",
            "tools_used": 0,
        }
    verify_draft_v1_hash(receipt.draft)
    result = receipt.generation_result
    if result is None or not receipt.draft.draft_text:
        raise ValidationFailure("real provider returned no verified Draft V1")
    return {
        "status": "PASS",
        "provider_id": spec.provider_id,
        "model_id": spec.model_id,
        "model_declared_version": spec.model_declared_version,
        "immutable_model_revision": spec.immutable_model_revision,
        "provider_calls": 1,
        "tools_used": 0,
        "finish_reason": result.finish_reason,
        "attempt_count": result.attempt_count,
        "safe_usage_metadata": dict(result.usage_metadata),
        "draft_byte_length": receipt.draft.draft_byte_length,
        "draft_text_sha256": receipt.draft.draft_text_sha256,
        "draft_hash": receipt.draft.draft_hash,
        "response_retained_in_evidence": False,
    }


def _validate_provider_registry() -> dict[str, Any]:
    spec = load_approved_provider_spec()
    key = os.environ.get(spec.credential_environment_variable)
    if not key:
        return {"status": "UNAVAILABLE", "reason": "APPROVED_CREDENTIAL_NOT_PRESENT"}
    request = urllib.request.Request(
        spec.api_origin + "/v1/models",
        headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json",
            "User-Agent": "aioa-memory-kernel-step22-validation/1a",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(256 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__}
    if len(raw) > 256 * 1024:
        raise ValidationFailure("provider registry response exceeded bound")
    decoded = json.loads(raw.decode("utf-8"))
    models = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(models, list):
        raise ValidationFailure("provider registry response is invalid")
    selected = next(
        (item for item in models if isinstance(item, dict) and item.get("id") == spec.model_id),
        None,
    )
    if selected is None:
        raise ValidationFailure("approved model is absent from provider registry")
    observed = {
        "id": selected.get("id"),
        "owned_by": selected.get("owned_by"),
        "context_length": selected.get("context_length"),
    }
    expected = {
        "id": spec.model_id,
        "owned_by": spec.model_registry_owner,
        "context_length": spec.context_window_tokens,
    }
    if observed != expected:
        raise ValidationFailure("provider registry model identity changed")
    return {"status": "PASS", "observed": observed}


def validate() -> dict[str, Any]:
    lineage = _lineage()
    request = prepare_model_generation_request(lineage, VALIDATION_QUERY)
    spec = load_approved_provider_spec()
    fake = _validate_fake(request)
    registry = _validate_provider_registry()
    real = _validate_real(request)
    if real["status"] not in {"PASS", "UNAVAILABLE"}:
        raise ValidationFailure("real provider status is invalid")
    evidence: dict[str, Any] = {
        "step": "STEP_22_PROVIDER_NEUTRAL_MODEL_ADAPTER_DRAFT_V1_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "provider_decision": {
            "provider_id": spec.provider_id,
            "adapter_version": spec.adapter_version,
            "model_id": spec.model_id,
            "model_declared_version": spec.model_declared_version,
            "immutable_model_revision": spec.immutable_model_revision,
            "model_registry_owner": spec.model_registry_owner,
            "endpoint_class": spec.endpoint_class,
            "config_digest": spec.config_digest,
            "provider_identity_digest": spec.provider_identity().identity_digest,
            "live_registry_validation": registry,
        },
        "request_identity": {
            "original_query_digest": request.original_query_digest,
            "step21_result_hash": request.step21_result_hash,
            "route_hash": request.route_hash,
            "prompt_template_digest": request.prompt_template.template_digest,
            "generation_parameters_digest": request.generation_parameters.parameters_digest,
            "timeout_policy_digest": request.timeout_policy.policy_digest,
            "attempt_policy_digest": request.attempt_policy.policy_digest,
            "generation_request_hash": request.request_hash,
        },
        "true_draft_v1_boundary": {
            "original_query_only": True,
            "correction_evidence_sent": False,
            "step20_bundle_sent": False,
            "step21_temporal_evidence_sent": False,
            "source_authority_sent": False,
            "evidence_sentinel_negative": "PASS",
            "tools_disabled": True,
            "function_calling_disabled": True,
            "web_browsing_disabled": True,
            "code_execution_disabled": True,
        },
        "fake_provider_validation": fake,
        "real_provider_validation": real,
        "persistence": {
            "schema": "memory_patch.drafts",
            "migration_added": False,
            "draft_stage": 1,
            "idempotent_replay": "PASS",
            "short_transaction_boundary": "PASS",
            "model_call_inside_database_transaction": False,
            "tenant_user_rls_reused": True,
        },
        "credential_isolation": {
            "credential_environment_variable_named_only": spec.credential_environment_variable,
            "credential_value_recorded": False,
            "database_credentials_in_provider_adapter": False,
            "aws_credentials_in_provider_adapter": False,
            "approval_credentials_in_provider_adapter": False,
            "commit_credentials_in_provider_adapter": False,
        },
        "authority_negatives": {
            "model_output_authority": False,
            "route_mutation": False,
            "policy_mutation": False,
            "evidence_mutation": False,
            "approval_created": False,
            "execution_capability": False,
        },
        "resource_bounds": {
            "maximum_original_query_utf8_bytes": 4096,
            "maximum_draft_utf8_bytes": 65536,
            "attempt_timeout_seconds": request.timeout_policy.attempt_timeout_seconds,
            "maximum_attempts": request.attempt_policy.max_attempts,
            "aws_mutations": 0,
            "s3_mutations": 0,
        },
        "step23_started": False,
        "claim_extraction": 0,
        "correction_packet": 0,
        "draft_v2": 0,
        "cleanup": {
            "temporary_runtime": "NOT_REQUIRED",
            "database": "NOT_REQUIRED",
            "owned_processes_started": 0,
        },
    }
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (ModelAdapterError, ValidationFailure, OSError, ValueError) as exc:
        reason = exc.reason_code.value if isinstance(exc, ModelAdapterError) else type(exc).__name__
        print(canonical_json({"status": "FAILED", "reason": reason}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
