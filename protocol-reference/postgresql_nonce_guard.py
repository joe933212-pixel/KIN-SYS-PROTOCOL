# Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
"""Shared PostgreSQL-backed atomic nonce consumption for federation and identity.

Every receiver worker must use the same PostgreSQL database. The unique
(tenant_id, nonce) key is the replay boundary; no process-local lock is trusted.
"""
from __future__ import annotations

import time
from contextlib import closing
from typing import Any, Callable


MAX_NONCE_TTL_SECONDS = 300


class PostgreSQLNonceGuardError(RuntimeError):
    """The shared replay store is unavailable or misconfigured."""


class PostgreSQLNonceGuard:
    """Atomic single-use nonce store backed by a shared PostgreSQL database.

    connection_factory must return a fresh, idle psycopg 3 connection. This
    adapter owns and closes each connection. Expired rows are pruned in the
    same transaction as the attempted insert.
    """

    def __init__(
        self, connection_factory: Callable[[], Any], *,
        clock: Callable[[], float] = time.time,
        max_ttl_seconds: int = MAX_NONCE_TTL_SECONDS,
    ):
        if not callable(connection_factory) or not callable(clock):
            raise PostgreSQLNonceGuardError("POSTGRES_NONCE_CONFIGURATION_INVALID")
        if (isinstance(max_ttl_seconds, bool) or not isinstance(max_ttl_seconds, int)
                or not 1 <= max_ttl_seconds <= 3600):
            raise PostgreSQLNonceGuardError("POSTGRES_NONCE_TTL_INVALID")
        self._connect = connection_factory
        self._clock = clock
        self._max_ttl = max_ttl_seconds

    def consume(
        self, tenant_id: str, nonce: str, expires_at: int | None = None
    ) -> bool:
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or len(tenant_id) > 128 or not isinstance(nonce, str)
                or not nonce.strip() or len(nonce) > 256):
            return False
        now = int(self._clock())
        expiry = (expires_at if type(expires_at) is int
                  else now + self._max_ttl)
        if expiry <= now or expiry - now > self._max_ttl:
            return False
        try:
            with closing(self._connect()) as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "DELETE FROM consumed_federation_nonces WHERE expires_at <= %s",
                            (now,),
                        )
                        cursor.execute(
                            """INSERT INTO consumed_federation_nonces
                               (tenant_id, nonce, expires_at, consumed_at)
                               VALUES (%s, %s, %s, %s)
                               ON CONFLICT (tenant_id, nonce) DO NOTHING
                               RETURNING nonce""",
                            (tenant_id, nonce, expiry, now),
                        )
                        return cursor.fetchone() is not None
        except Exception as exc:
            raise PostgreSQLNonceGuardError("POSTGRES_NONCE_STORE_UNAVAILABLE") from exc
