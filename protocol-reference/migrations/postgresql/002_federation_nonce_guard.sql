-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Shared, atomic replay protection for federated and identity assertions.
-- Expiry is Unix epoch seconds to match the signed protocol claims.

CREATE TABLE IF NOT EXISTS consumed_federation_nonces (
    tenant_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at BIGINT NOT NULL CHECK (expires_at > 0),
    consumed_at BIGINT NOT NULL CHECK (consumed_at > 0),
    PRIMARY KEY (tenant_id, nonce)
);

CREATE INDEX IF NOT EXISTS consumed_federation_nonces_expiry
    ON consumed_federation_nonces (expires_at);
