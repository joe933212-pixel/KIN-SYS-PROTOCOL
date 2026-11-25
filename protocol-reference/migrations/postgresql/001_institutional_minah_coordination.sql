-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- PostgreSQL schema foundation for the shared institutional MINAH lifecycle,
-- lease and fencing authority. This migration is not a runtime adapter.
-- Apply only through a controlled migration runner after review.


CREATE TABLE IF NOT EXISTS minah_deployment_versions (
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'REVOKED')),
    config_json JSONB NOT NULL,
    digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, deployment_id, version)
);

CREATE TABLE IF NOT EXISTS minah_deployment_heads (
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    current_version TEXT NOT NULL,
    PRIMARY KEY (tenant_id, deployment_id),
    FOREIGN KEY (tenant_id, deployment_id, current_version)
        REFERENCES minah_deployment_versions (tenant_id, deployment_id, version)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS minah_deployment_epochs (
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    epoch BIGINT NOT NULL CHECK (epoch >= 1),
    PRIMARY KEY (tenant_id, deployment_id),
    FOREIGN KEY (tenant_id, deployment_id)
        REFERENCES minah_deployment_heads (tenant_id, deployment_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS minah_deployment_fence_sequences (
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    last_token BIGINT NOT NULL CHECK (last_token >= 0),
    PRIMARY KEY (tenant_id, deployment_id),
    FOREIGN KEY (tenant_id, deployment_id)
        REFERENCES minah_deployment_heads (tenant_id, deployment_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS minah_execution_leases (
    lease_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    epoch BIGINT NOT NULL CHECK (epoch >= 1),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    token_digest TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, deployment_id)
        REFERENCES minah_deployment_heads (tenant_id, deployment_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    UNIQUE (tenant_id, deployment_id, fencing_token)
);

CREATE INDEX IF NOT EXISTS minah_execution_leases_scope
    ON minah_execution_leases (tenant_id, deployment_id, epoch, expires_at);

CREATE TABLE IF NOT EXISTS minah_deployment_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, deployment_id, version)
        REFERENCES minah_deployment_versions (tenant_id, deployment_id, version)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

