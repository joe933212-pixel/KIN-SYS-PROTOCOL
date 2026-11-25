-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable PostgreSQL CEE lifecycle journal. IVM remains a separate store;
-- this schema does not claim a distributed transaction across the two.

CREATE TABLE IF NOT EXISTS cee_operations (
    operation_id TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    request_json JSONB NOT NULL,
    phase TEXT NOT NULL,
    minah_json JSONB,
    ivm_json JSONB,
    outcome TEXT,
    result_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cee_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES cee_operations(operation_id),
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (operation_id, phase)
);

CREATE INDEX IF NOT EXISTS cee_events_operation_order
    ON cee_events(operation_id, event_id);

CREATE TABLE IF NOT EXISTS cee_observations (
    observation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id TEXT NOT NULL REFERENCES cee_operations(operation_id),
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS cee_observations_operation_order
    ON cee_observations(operation_id, observation_id);

CREATE OR REPLACE FUNCTION kin_sys_reject_cee_append_only_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KIN_SYS_CEE_APPEND_ONLY_RECORD';
END;
$$;

DROP TRIGGER IF EXISTS cee_events_no_update ON cee_events;
CREATE TRIGGER cee_events_no_update
BEFORE UPDATE OR DELETE ON cee_events
FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_cee_append_only_mutation();

DROP TRIGGER IF EXISTS cee_observations_no_update ON cee_observations;
CREATE TRIGGER cee_observations_no_update
BEFORE UPDATE OR DELETE ON cee_observations
FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_cee_append_only_mutation();
