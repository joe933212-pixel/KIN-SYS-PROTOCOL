-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Fail-closed tenant isolation for durable federation delivery state.

ALTER TABLE federation_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE federation_inbox FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON federation_inbox
    USING (receiver_tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (receiver_tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

ALTER TABLE federation_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE federation_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY kin_sys_tenant_isolation ON federation_outbox
    USING (sender_tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''))
    WITH CHECK (sender_tenant_id = NULLIF(current_setting('kin_sys.tenant_id', true), ''));

-- Tenant context is transaction-local and is set by the inbox/outbox adapters.
-- Missing or empty context evaluates to NULL and denies row visibility and writes.
