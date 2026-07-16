-- Bind every existing staff identity to the legacy Viltrox tenant before
-- authenticated GTM reads require an explicit, unambiguous membership.
-- The migration runner owns the transaction and fleet advisory lock.  Do not
-- add BEGIN/COMMIT here.
INSERT INTO organization_members (organization_id, staff_id, role)
SELECT
    1,
    s.id,
    CASE
        WHEN COALESCE(s.is_owner, 0) = 1 THEN 'owner'
        WHEN LOWER(COALESCE(s.role, '')) = 'admin' THEN 'admin'
        WHEN LOWER(COALESCE(s.role, '')) = 'readonly' THEN 'viewer'
        ELSE 'member'
    END
FROM staff AS s
WHERE NOT EXISTS (
    SELECT 1
    FROM organization_members AS existing
    WHERE existing.staff_id = s.id
)
ON CONFLICT (organization_id, staff_id) DO NOTHING;
