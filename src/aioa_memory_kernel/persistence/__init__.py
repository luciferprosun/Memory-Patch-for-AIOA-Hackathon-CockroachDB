"""Retry-safe, authority-neutral persistence foundation.

No connection driver, DSN, credential, pool, model call, external action, or
approval/commit authority is created by this package.
"""

from .cockroach import CockroachPersistenceRepository
from .errors import (
    IdempotencyConflictError,
    ImmutableRecordConflictError,
    OperationStateConflictError,
    PersistenceConfigurationError,
    PersistenceError,
    PersistenceTransactionError,
    RetryableSerializationError,
    RetryExhaustedError,
    TransactionBoundaryViolation,
)
from .idempotency import IdempotencyService, operation_from_row
from .models import (
    AccessMode,
    AuditEventRecord,
    BeginOperation,
    DraftRecord,
    DraftV2Record,
    EvidenceItemRecord,
    ExternalReferenceIdentity,
    KernelRunRecord,
    OperationClaim,
    OperationStatus,
    PersistenceOperation,
    RequestContext,
    SourceSnapshotRecord,
    digest_canonical_request,
)
from .retry import RetryPolicy, extract_sqlstate, is_retryable_sqlstate
from .transaction import (
    SerializableTransactionRunner,
    Transaction,
    assert_no_open_persistence_transaction,
)


__all__ = [
    "AccessMode",
    "AuditEventRecord",
    "BeginOperation",
    "CockroachPersistenceRepository",
    "DraftRecord",
    "DraftV2Record",
    "EvidenceItemRecord",
    "ExternalReferenceIdentity",
    "IdempotencyConflictError",
    "IdempotencyService",
    "ImmutableRecordConflictError",
    "KernelRunRecord",
    "OperationClaim",
    "OperationStateConflictError",
    "OperationStatus",
    "PersistenceConfigurationError",
    "PersistenceError",
    "PersistenceOperation",
    "PersistenceTransactionError",
    "RequestContext",
    "RetryExhaustedError",
    "RetryPolicy",
    "RetryableSerializationError",
    "SerializableTransactionRunner",
    "SourceSnapshotRecord",
    "Transaction",
    "TransactionBoundaryViolation",
    "assert_no_open_persistence_transaction",
    "digest_canonical_request",
    "extract_sqlstate",
    "is_retryable_sqlstate",
    "operation_from_row",
]
