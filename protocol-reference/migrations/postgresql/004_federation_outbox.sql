-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable sender-side federation delivery and acknowledgement state.

CREATE TABLE IF NOT EXISTS federation_outbox (
    sender_tenant_id TEXT NOT NULL,
    sender_installation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    receiver_installation_id TEXT NOT NULL,
    receiver_institution_id TEXT NOT NULL,
    envelope_json JSONB NOT NULL,
    envelope_sha256 TEXT NOT NULL CHECK (length(envelope_sha256) = 64),
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'DELIVERING', 'ACKNOWLEDGED', 'RETRYABLE_FAILURE')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count BIGINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    acknowledgement_json JSONB,
    acknowledgement_sha256 TEXT,
    acknowledged_at TIMESTAMPTZ,
    last_error_code TEXT,
    PRIMARY KEY (sender_tenant_id, sender_installation_id, message_id),
    UNIQUE (sender_tenant_id, sender_installation_id, idempotency_key),
    CHECK (
        (status = 'DELIVERING' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'DELIVERING'
    ),
    CHECK (
        (status = 'ACKNOWLEDGED' AND acknowledgement_json IS NOT NULL
         AND acknowledgement_sha256 IS NOT NULL AND acknowledged_at IS NOT NULL)
        OR status <> 'ACKNOWLEDGED'
    )
);

CREATE INDEX IF NOT EXISTS federation_outbox_claimable
    ON federation_outbox (sender_tenant_id, status, created_at);

CREATE INDEX IF NOT EXISTS federation_outbox_expired_leases
    ON federation_outbox (sender_tenant_id, lease_expires_at)
    WHERE status = 'DELIVERING';
