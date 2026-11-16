-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Tenant-scoped authority grants, issuer trust roots, key references and retained provenance.

CREATE TABLE authority_grant_versions (
    tenant_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','ACTIVE','SUSPENDED','REVOKED')),
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, grant_id, version),
    CHECK (payload_json->>'tenant_id' = tenant_id),
    CHECK (payload_json->>'grant_id' = grant_id),
    CHECK (payload_json->>'version' = version),
    CHECK (payload_json->>'status' = status)
);

CREATE TABLE authority_grant_heads (
    tenant_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    current_version TEXT NOT NULL,
    PRIMARY KEY (tenant_id, grant_id),
    FOREIGN KEY (tenant_id, grant_id, current_version)
        REFERENCES authority_grant_versions (tenant_id, grant_id, version)
);

CREATE TABLE authority_grant_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, event_id),
    UNIQUE (tenant_id, grant_id, version),
    FOREIGN KEY (tenant_id, grant_id, version)
        REFERENCES authority_grant_versions (tenant_id, grant_id, version)
);

CREATE INDEX authority_grant_events_resource
    ON authority_grant_events (tenant_id, grant_id, created_at, event_id);

CREATE TABLE accepted_grant_provenance (
    tenant_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    version TEXT NOT NULL,
    grant_digest TEXT NOT NULL CHECK (grant_digest ~ '^[a-f0-9]{64}$'),
    issuer_envelope_json JSONB NOT NULL CHECK (jsonb_typeof(issuer_envelope_json) = 'object'),
    acceptance_json JSONB NOT NULL CHECK (jsonb_typeof(acceptance_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, grant_id, version),
    FOREIGN KEY (tenant_id, grant_id, version)
        REFERENCES authority_grant_versions (tenant_id, grant_id, version)
);

CREATE TABLE trusted_issuer_relationships (
    tenant_id TEXT NOT NULL,
    issuer_institution_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
    trust_basis_ref TEXT NOT NULL,
    valid_from BIGINT NOT NULL,
    valid_until BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, issuer_institution_id),
    CHECK (valid_until > valid_from)
);

CREATE TABLE trusted_issuer_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    issuer_institution_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, issuer_institution_id)
        REFERENCES trusted_issuer_relationships (tenant_id, issuer_institution_id)
);

CREATE TABLE institutional_trust_keys (
    tenant_id TEXT NOT NULL,
    issuer_institution_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    algorithm TEXT NOT NULL CHECK (algorithm = 'Ed25519'),
    public_key_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REVOKED')),
    valid_from BIGINT NOT NULL,
    valid_until BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, key_id),
    FOREIGN KEY (tenant_id, issuer_institution_id)
        REFERENCES trusted_issuer_relationships (tenant_id, issuer_institution_id),
    CHECK (valid_until > valid_from)
);

CREATE INDEX institutional_trust_keys_issuer
    ON institutional_trust_keys (tenant_id, issuer_institution_id, status);

CREATE TABLE institutional_trust_key_events (
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, event_id),
    FOREIGN KEY (tenant_id, key_id)
        REFERENCES institutional_trust_keys (tenant_id, key_id)
);

CREATE FUNCTION kin_sys_reject_immutable_trust_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'KIN_SYS_IMMUTABLE_TRUST_RECORD';
END;
$$;

CREATE TRIGGER authority_grant_versions_immutable
    BEFORE UPDATE OR DELETE ON authority_grant_versions
    FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_immutable_trust_mutation();

CREATE TRIGGER authority_grant_events_immutable
    BEFORE UPDATE OR DELETE ON authority_grant_events
    FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_immutable_trust_mutation();

CREATE TRIGGER accepted_grant_provenance_immutable
    BEFORE UPDATE OR DELETE ON accepted_grant_provenance
    FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_immutable_trust_mutation();

CREATE TRIGGER trusted_issuer_events_immutable
    BEFORE UPDATE OR DELETE ON trusted_issuer_events
    FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_immutable_trust_mutation();

CREATE TRIGGER institutional_trust_key_events_immutable
    BEFORE UPDATE OR DELETE ON institutional_trust_key_events
    FOR EACH ROW EXECUTE FUNCTION kin_sys_reject_immutable_trust_mutation();

CREATE FUNCTION kin_sys_allow_one_way_trust_revocation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'KIN_SYS_TRUST_RECORD_DELETE_FORBIDDEN';
    END IF;
    IF OLD.status <> 'ACTIVE'
       OR NEW.status <> 'REVOKED'
       OR (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
        RAISE EXCEPTION 'KIN_SYS_TRUST_RECORD_REVOCATION_ONLY';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trusted_issuer_relationships_revoke_only
    BEFORE UPDATE OR DELETE ON trusted_issuer_relationships
    FOR EACH ROW EXECUTE FUNCTION kin_sys_allow_one_way_trust_revocation();

CREATE TRIGGER institutional_trust_keys_revoke_only
    BEFORE UPDATE OR DELETE ON institutional_trust_keys
    FOR EACH ROW EXECUTE FUNCTION kin_sys_allow_one_way_trust_revocation();

CREATE FUNCTION kin_sys_protect_grant_head_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'KIN_SYS_GRANT_HEAD_DELETE_FORBIDDEN';
    END IF;
    IF NEW.tenant_id <> OLD.tenant_id OR NEW.grant_id <> OLD.grant_id THEN
        RAISE EXCEPTION 'KIN_SYS_GRANT_HEAD_IDENTITY_IMMUTABLE';
    END IF;
    IF NEW.current_version = OLD.current_version THEN
        RAISE EXCEPTION 'KIN_SYS_GRANT_HEAD_VERSION_MUST_ADVANCE';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER authority_grant_heads_identity_protected
    BEFORE UPDATE OR DELETE ON authority_grant_heads
    FOR EACH ROW EXECUTE FUNCTION kin_sys_protect_grant_head_identity();

CREATE FUNCTION kin_sys_require_grant_head_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    event_payload JSONB;
BEGIN
    SELECT payload_json INTO event_payload
    FROM authority_grant_events
    WHERE tenant_id = NEW.tenant_id
      AND grant_id = NEW.grant_id
      AND version = NEW.current_version;
    IF event_payload IS NULL THEN
        RAISE EXCEPTION 'KIN_SYS_GRANT_HEAD_AUDIT_EVENT_REQUIRED';
    END IF;
    IF TG_OP = 'UPDATE' AND event_payload->>'from_version' IS DISTINCT FROM OLD.current_version THEN
        RAISE EXCEPTION 'KIN_SYS_GRANT_HEAD_MUST_ADVANCE_FROM_CURRENT_VERSION';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER authority_grant_head_event_required
    AFTER INSERT OR UPDATE ON authority_grant_heads
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION kin_sys_require_grant_head_event();


CREATE FUNCTION kin_sys_validate_trust_key_window()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    root trusted_issuer_relationships%ROWTYPE;
BEGIN
    SELECT * INTO root
    FROM trusted_issuer_relationships
    WHERE tenant_id = NEW.tenant_id
      AND issuer_institution_id = NEW.issuer_institution_id
    FOR SHARE;
    IF NOT FOUND OR root.status <> 'ACTIVE'
       OR NEW.valid_from < root.valid_from
       OR NEW.valid_until > root.valid_until THEN
        RAISE EXCEPTION 'KIN_SYS_TRUST_KEY_OUTSIDE_ISSUER_TRUST_WINDOW';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER institutional_trust_keys_window_valid
    BEFORE INSERT ON institutional_trust_keys
    FOR EACH ROW EXECUTE FUNCTION kin_sys_validate_trust_key_window();


CREATE FUNCTION kin_sys_require_issuer_trust_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    required_event TEXT;
BEGIN
    required_event := CASE WHEN TG_OP = 'INSERT'
        THEN 'ISSUER_TRUST_ESTABLISHED' ELSE 'ISSUER_TRUST_REVOKED' END;
    IF NOT EXISTS (
        SELECT 1 FROM trusted_issuer_events
        WHERE tenant_id = NEW.tenant_id
          AND issuer_institution_id = NEW.issuer_institution_id
          AND event_type = required_event
    ) THEN
        RAISE EXCEPTION 'KIN_SYS_ISSUER_TRUST_AUDIT_EVENT_REQUIRED';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trusted_issuer_event_required
    AFTER INSERT OR UPDATE ON trusted_issuer_relationships
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION kin_sys_require_issuer_trust_event();

CREATE FUNCTION kin_sys_require_trust_key_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    required_event TEXT;
BEGIN
    required_event := CASE WHEN TG_OP = 'INSERT'
        THEN 'TRUST_KEY_REGISTERED' ELSE 'TRUST_KEY_REVOKED' END;
    IF NOT EXISTS (
        SELECT 1 FROM institutional_trust_key_events
        WHERE tenant_id = NEW.tenant_id
          AND key_id = NEW.key_id
          AND event_type = required_event
    ) THEN
        RAISE EXCEPTION 'KIN_SYS_TRUST_KEY_AUDIT_EVENT_REQUIRED';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER institutional_trust_key_event_required
    AFTER INSERT OR UPDATE ON institutional_trust_keys
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION kin_sys_require_trust_key_event();
