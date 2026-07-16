-- Intentional no-op. Membership rows are security identities and cannot be
-- distinguished safely from administrator-created rows after the backfill.
BEGIN;
COMMIT;
