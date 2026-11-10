-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable receiver-side delivery state. This is not a cross-store exactly-once guarantee.

CREATE TABLE IF NOT EXISTS federation_inbox (
    receiver_tenant_id TEXT NOT NULL,
    issuer_installation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    envelope_json JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('RECEIVED', 'PROCESSING', 'COMPLETED', 'REJECTED', 'RETRYABLE_FAILURE')
    ),
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count BIGINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    receiver_decision_ref TEXT,
    response_json JSONB,
    last_error_code TEXT,
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (receiver_tenant_id, issuer_installation_id, message_id),
    UNIQUE (receiver_tenant_id, issuer_installation_id, idempotency_key),
    CHECK (
        (status = 'PROCESSING' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR status <> 'PROCESSING'
    ),
    CHECK (
        (status = 'COMPLETED' AND receiver_decision_ref IS NOT NULL AND completed_at IS NOT NULL)
        OR status <> 'COMPLETED'
    )
);

CREATE INDEX IF NOT EXISTS federation_inbox_claimable
    ON federation_inbox (receiver_tenant_id, status, received_at);

CREATE INDEX IF NOT EXISTS federation_inbox_expired_leases
    ON federation_inbox (receiver_tenant_id, lease_expires_at)
    WHERE status = 'PROCESSING';
