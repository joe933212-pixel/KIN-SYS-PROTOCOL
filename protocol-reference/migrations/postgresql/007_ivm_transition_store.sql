-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable PostgreSQL IVM transition store. Separate from CEE; no distributed
-- transaction is implied between these independent persistence boundaries.

CREATE TABLE IF NOT EXISTS ivm_processes (
    process_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    authority_id TEXT NOT NULL,
    authority_active BOOLEAN NOT NULL,
    rule_set TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    eligible BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS ivm_events (
    event_id TEXT PRIMARY KEY,
    process_id TEXT NOT NULL REFERENCES ivm_processes(process_id),
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    actor_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ACCEPTED','REJECTED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ivm_transition_history (
    history_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE REFERENCES ivm_events(event_id),
    process_id TEXT NOT NULL REFERENCES ivm_processes(process_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    rule_set TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ivm_history_process_order
    ON ivm_transition_history(process_id, created_at, history_id);

CREATE TABLE IF NOT EXISTS ivm_execution_fences (
    tenant_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    highest_token BIGINT NOT NULL CHECK (highest_token >= 1),
    highest_epoch BIGINT NOT NULL CHECK (highest_epoch >= 1),
    PRIMARY KEY (tenant_id, deployment_id)
);

CREATE OR REPLACE FUNCTION kin_sys_reject_ivm_history_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KIN_SYS_IVM_HISTORY_APPEND_ONLY';
END;
$$;

DROP TRIGGER IF EXISTS ivm_history_no_update ON ivm_transition_history;
CREATE TRIGGER ivm_history_no_update
BEFORE UPDATE OR DELETE ON ivm_transition_history
FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_ivm_history_mutation();
