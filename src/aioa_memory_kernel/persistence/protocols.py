"""Narrow DB-API-style protocols; no driver or credential policy is embedded."""

from __future__ import annotations

from typing import Callable, Mapping, Protocol, Sequence, TypeVar


Parameters = Sequence[object] | Mapping[str, object] | None
Row = Mapping[str, object]
T = TypeVar("T")


class CursorProtocol(Protocol):
    def execute(self, sql: str, parameters: Parameters = None) -> object: ...

    def fetchone(self) -> Row | None: ...

    def fetchall(self) -> Sequence[Row]: ...

    def close(self) -> None: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], ConnectionProtocol]


class TransactionProtocol(Protocol):
    @property
    def active(self) -> bool: ...

    def execute(self, sql: str, parameters: Parameters = None) -> None: ...

    def fetch_one(self, sql: str, parameters: Parameters = None) -> Row | None: ...

    def fetch_all(
        self,
        sql: str,
        parameters: Parameters = None,
    ) -> tuple[Row, ...]: ...
