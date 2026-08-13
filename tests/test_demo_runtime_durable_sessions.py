"""Focused R4 durable, bounded session tests."""

from __future__ import annotations

import inspect
import unittest

from aioa_memory_kernel.personal_memory_ui import OwnerPrincipal
from aioa_memory_kernel.personal_memory_ui.cockroach_sessions import (
    AuthenticatedSessionRecord,
    CockroachOwnerSessionRepository,
    CockroachOwnerSessionStore,
    DurableSessionError,
    DurableSessionLimits,
    PendingSessionRecord,
)


OWNER_A = OwnerPrincipal("tenant-a", "owner-a", "subject-a", "Owner A")
OWNER_B = OwnerPrincipal("tenant-a", "owner-b", "subject-b", "Owner B")


class CleanupCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, sql, parameters=None):
        self.events.append("begin" if sql.startswith("BEGIN") else "execute")

    def close(self):
        self.events.append("cursor-close")


class CleanupConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self._cursor = CleanupCursor(events)

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")

    def close(self):
        self.events.append("connection-close")


class FakeDurableRepository:
    def __init__(self) -> None:
        self.pending: dict[str, PendingSessionRecord] = {}
        self.authenticated: dict[str, AuthenticatedSessionRecord] = {}
        self.raw_handles: set[str] = set()

    def _purge(self, now) -> None:
        self.pending = {
            key: value for key, value in self.pending.items() if value.expires_at > now
        }
        self.authenticated = {
            key: value
            for key, value in self.authenticated.items()
            if value.expires_at > now
        }

    def insert_pending(self, record, limits):
        self._purge(record.created_at)
        if (
            len(self.pending) >= limits.maximum_pending_flows
            or len(self.pending) + len(self.authenticated)
            >= limits.maximum_total_records
            or record.handle_hash in self.pending
            or record.handle_hash in self.authenticated
        ):
            return False
        self.pending[record.handle_hash] = record
        return True

    def consume_pending(self, handle_hash, now):
        self._purge(now)
        return self.pending.pop(handle_hash, None)

    def insert_authenticated(self, record, limits):
        self._purge(record.created_at)
        owner_count = sum(
            value.principal.tenant_id == record.principal.tenant_id
            and value.principal.owner_user_id == record.principal.owner_user_id
            for value in self.authenticated.values()
        )
        if (
            owner_count >= limits.maximum_sessions_per_owner
            or len(self.pending) + len(self.authenticated)
            >= limits.maximum_total_records
            or record.handle_hash in self.pending
            or record.handle_hash in self.authenticated
        ):
            return False
        self.authenticated[record.handle_hash] = record
        return True

    def get_authenticated(self, handle_hash, now):
        self._purge(now)
        return self.authenticated.get(handle_hash)

    def delete(self, handle_hash):
        self.pending.pop(handle_hash, None)
        self.authenticated.pop(handle_hash, None)


class DurableSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeDurableRepository()
        self.limits = DurableSessionLimits(
            absolute_ttl_seconds=900,
            pending_ttl_seconds=60,
            maximum_total_records=4,
            maximum_pending_flows=2,
            maximum_sessions_per_owner=2,
            maximum_payload_bytes=2048,
        )
        self.store = CockroachOwnerSessionStore(
            limits=self.limits, repository=self.repository
        )

    def test_pending_is_hashed_single_use_and_safe_return_path(self):
        handle, pending = self.store.create_pending(
            return_path="//attacker.invalid", now=100.0
        )
        self.assertNotIn(handle, self.repository.pending)
        self.assertEqual(pending.return_path, "/memory")
        self.assertEqual(self.store.consume_pending(handle, now=101.0), pending)
        self.assertIsNone(self.store.consume_pending(handle, now=101.0))

    def test_authenticated_session_survives_store_restart(self):
        handle, created = self.store.create_session(OWNER_A, now=100.0)
        restarted = CockroachOwnerSessionStore(
            limits=self.limits, repository=self.repository
        )
        restored = restarted.get_session(handle, now=101.0)
        self.assertEqual(restored, created)
        self.assertEqual(restored.principal, OWNER_A)

    def test_revoked_expired_unknown_and_malformed_handles_fail_closed(self):
        revoked_handle, _ = self.store.create_session(OWNER_A, now=100.0)
        self.store.delete_session(revoked_handle)
        self.assertIsNone(self.store.get_session(revoked_handle, now=101.0))

        expired_handle, created = self.store.create_session(OWNER_A, now=100.0)
        self.assertIsNone(
            self.store.get_session(expired_handle, now=created.expires_at + 1)
        )
        self.assertIsNone(self.store.get_session("unknown", now=101.0))
        self.assertIsNone(self.store.get_session("\N{SNOWMAN}", now=101.0))
        self.assertIsNone(self.store.get_session("x" * 257, now=101.0))

    def test_per_owner_and_global_capacity_are_bounded(self):
        self.store.create_session(OWNER_A, now=100.0)
        self.store.create_session(OWNER_A, now=100.0)
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            self.store.create_session(OWNER_A, now=100.0)
        self.store.create_session(OWNER_B, now=100.0)
        self.store.create_pending(return_path="/memory", now=100.0)
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            self.store.create_pending(return_path="/memory", now=100.0)

    def test_cross_owner_handle_never_switches_identity(self):
        handle_a, _ = self.store.create_session(OWNER_A, now=100.0)
        handle_b, _ = self.store.create_session(OWNER_B, now=100.0)
        self.assertEqual(
            self.store.get_session(handle_a, now=101.0).principal, OWNER_A
        )
        self.assertEqual(
            self.store.get_session(handle_b, now=101.0).principal, OWNER_B
        )
        self.assertNotEqual(handle_a, handle_b)

    def test_close_is_idempotent_and_does_not_close_shared_repository(self):
        self.store.close()
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.store.get_session("unknown", now=100.0)
        self.assertFalse(hasattr(self.repository, "closed"))

    def test_sql_repository_is_parameterized_and_has_no_authority_fallback(self):
        source = inspect.getsource(CockroachOwnerSessionRepository)
        self.assertIn("%s", source)
        self.assertNotIn("DATABASE_URL_MIGRATOR", source)
        self.assertNotIn("mp_schema_owner", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("admin", source.casefold())

    def test_sql_repository_failure_is_sanitized_and_closes_owned_lease(self):
        events: list[str] = []
        repository = CockroachOwnerSessionRepository(
            lambda: CleanupConnection(events)
        )

        def fail(_cursor):
            raise ValueError("synthetic-secret-must-not-escape")

        with self.assertRaises(DurableSessionError) as raised:
            repository._run(fail)
        self.assertEqual(str(raised.exception), "durable session storage failed safely")
        self.assertNotIn("synthetic-secret", str(raised.exception))
        self.assertEqual(
            events,
            ["begin", "rollback", "cursor-close", "connection-close"],
        )


if __name__ == "__main__":
    unittest.main()
