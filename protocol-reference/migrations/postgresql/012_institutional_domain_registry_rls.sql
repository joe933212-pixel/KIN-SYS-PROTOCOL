-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Fail-closed row-level tenant isolation for institutional domain records/events.

ALTER TABLE institutional_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_records FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON institutional_records
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE institutional_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE institutional_events FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON institutional_events
    USING (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

-- The PostgreSQL registry adapter sets kin_sys.tenant_id transaction-locally.
-- Missing or empty context yields NULL and denies reads and writes.
