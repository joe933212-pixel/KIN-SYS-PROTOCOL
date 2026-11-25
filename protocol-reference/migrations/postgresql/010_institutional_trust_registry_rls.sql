-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Fail-closed row-level tenant isolation for the institutional trust registry.

ALTER TABLE authority_grant_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE authority_grant_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON authority_grant_versions
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE authority_grant_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE authority_grant_heads FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON authority_grant_heads
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE authority_grant_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE authority_grant_events FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON authority_grant_events
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE accepted_grant_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE accepted_grant_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON accepted_grant_provenance
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE trusted_issuer_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_issuer_relationships FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON trusted_issuer_relationships
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE trusted_issuer_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE trusted_issuer_events FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON trusted_issuer_events
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE institutional_trust_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_trust_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON institutional_trust_keys
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE institutional_trust_key_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_trust_key_events FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON institutional_trust_key_events
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

-- The application adapter sets kin_sys.tenant_id transaction-locally. An unset
-- or empty setting yields NULL and therefore denies row visibility and writes.
