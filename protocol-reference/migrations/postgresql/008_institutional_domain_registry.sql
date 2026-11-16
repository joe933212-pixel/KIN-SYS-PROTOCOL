-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable tenant-scoped institutional domain records and lifecycle evidence.
CREATE TABLE IF NOT EXISTS institutional_records (
    record_order BIGSERIAL NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('institution','jurisdiction','minah','binding')),
    resource_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, resource_type, resource_id, version)
);
CREATE INDEX IF NOT EXISTS institutional_records_lookup
    ON institutional_records (tenant_id, resource_type, resource_id, record_order DESC);

CREATE TABLE IF NOT EXISTS institutional_events (
    event_order BIGSERIAL NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    resource_type TEXT NOT NULL CHECK (resource_type IN ('institution','jurisdiction','minah','binding')),
    resource_id TEXT NOT NULL,
    version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    evidence_ref TEXT,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, resource_type, resource_id, version)
        REFERENCES institutional_records (tenant_id, resource_type, resource_id, version)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS institutional_events_lookup
    ON institutional_events (tenant_id, resource_type, resource_id, event_order);

CREATE OR REPLACE FUNCTION kin_sys_reject_institutional_record_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KIN_SYS_INSTITUTIONAL_RECORD_APPEND_ONLY';
END;
$$;
DROP TRIGGER IF EXISTS institutional_records_append_only ON institutional_records;
CREATE TRIGGER institutional_records_append_only
BEFORE UPDATE OR DELETE ON institutional_records
FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_institutional_record_mutation();

CREATE OR REPLACE FUNCTION kin_sys_reject_institutional_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'KIN_SYS_INSTITUTIONAL_EVENT_APPEND_ONLY';
END;
$$;
DROP TRIGGER IF EXISTS institutional_events_append_only ON institutional_events;
CREATE TRIGGER institutional_events_append_only
BEFORE UPDATE OR DELETE ON institutional_events
FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_institutional_event_mutation();
