-- 216_vkpi_evidence_publish_date_down.sql — 回滚 216:删归一列与索引。
-- 纯 additive 回退:published_at_norm 是 posted_at/publish_date 的派生副本,
-- 删除无数据损失,旧路径列原样保留;读端 COALESCE 自动落回旧路径。
BEGIN;

DROP INDEX IF EXISTS idx_vkpi_evidence_published_at_norm;

ALTER TABLE vkpi_kol_video_evidence
    DROP COLUMN IF EXISTS published_at_norm;

COMMIT;
