-- 195 down — 移除 Tenant Kernel(谨慎:仅在确认无第二租户数据时)。
BEGIN;
DROP TABLE IF EXISTS organization_members;
DROP TABLE IF EXISTS organizations;
COMMIT;
