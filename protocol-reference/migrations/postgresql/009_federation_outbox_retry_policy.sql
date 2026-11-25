-- Copyright © 2026 Joel Kagoro. Licensed under Apache-2.0; see LICENSE and NOTICE.
-- Durable sender retry scheduling and bounded delivery attempts.

ALTER TABLE federation_outbox
    DROP CONSTRAINT IF EXISTS federation_outbox_status_check;

ALTER TABLE federation_outbox
    ADD CONSTRAINT federation_outbox_status_check
    CHECK (status IN (
        'PENDING', 'DELIVERING', 'ACKNOWLEDGED',
        'RETRYABLE_FAILURE', 'DEAD_LETTER'
    ));

ALTER TABLE federation_outbox
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS max_attempts SMALLINT NOT NULL DEFAULT 8,
    ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ;

ALTER TABLE federation_outbox
    ADD CONSTRAINT federation_outbox_max_attempts_range
    CHECK (max_attempts BETWEEN 1 AND 100);

ALTER TABLE federation_outbox
    ADD CONSTRAINT federation_outbox_dead_letter_state
    CHECK (
        (status = 'DEAD_LETTER' AND dead_lettered_at IS NOT NULL
         AND lease_owner IS NULL AND lease_expires_at IS NULL
         AND next_attempt_at IS NULL)
        OR status <> 'DEAD_LETTER'
    );

CREATE INDEX IF NOT EXISTS federation_outbox_retry_due
    ON federation_outbox (sender_tenant_id, sender_installation_id, next_attempt_at)
    WHERE status = 'RETRYABLE_FAILURE';

CREATE INDEX IF NOT EXISTS federation_outbox_dead_lettered
    ON federation_outbox (sender_tenant_id, sender_installation_id, dead_lettered_at)
    WHERE status = 'DEAD_LETTER';
