-- 139 回滚:仅 DROP 本迁移新增的 P1 隔离性能索引,无表/列变更需逆转。幂等。
DROP INDEX IF EXISTS idx_iso_kol_pool_favorites_staff_pool;
DROP INDEX IF EXISTS idx_iso_project_kol_assignments_project_pool;
DROP INDEX IF EXISTS idx_iso_kol_video_evidence_pool;
