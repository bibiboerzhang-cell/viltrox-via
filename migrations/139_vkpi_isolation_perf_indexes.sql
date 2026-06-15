-- 139(T3 P1 隔离子查询性能索引)。纯加速、additive only、幂等。
-- 红线:零触 viltrox_fit_score / rule_v0 / KOL Pool 评分;无表/列变更,仅 CREATE INDEX IF NOT EXISTS。
-- 回滚见 139_vkpi_isolation_perf_indexes_down.sql(逐条 DROP INDEX IF EXISTS)。
--
-- 服务对象:仪表盘隔离(P1 own-only 过滤)子查询的连接/过滤热路径:
--   account_picker.py:295-296 / summary.py:575-576
--     SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id=?
--     UNION SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a ...
--   summary.py:329/351/365/441/519/725 多处 FROM vkpi_kol_video_evidence e (按 kol_pool_id 连接)。
--
-- 按「列存在 + 非冗余」逐条决策(已核 107/084/085 建表 + 现存索引):
--  [建] vkpi_kol_pool_favorites(staff_id, kol_pool_id):现存仅 UNIQUE(kol_pool_id, staff_id)
--       与 idx_vkpi_kol_pool_favorites_staff(staff_id, created_at DESC);
--       本复合最左 staff_id 等值 + 覆盖列 kol_pool_id,直接服务「某 staff 的收藏 kol 集合」
--       子查询(无需回表),UNIQUE 最左是 kol_pool_id 不可用,非冗余。
--  [建] vkpi_project_kol_assignments(project_id, kol_pool_id):现存 idx_pka_project(project_id)、
--       idx_pka_kol_pool(kol_pool_id) 单列 + UNIQUE(project_id, kol_pool_id)。
--       UNIQUE 已物理覆盖本复合;此处用独立命名 idx_iso_* 显式声明隔离读路径意图,
--       IF NOT EXISTS 幂等且零风险(PG 不会重复建同定义索引时另立物理副本除非名不同——此处名不同,
--       属冗余但无害;保留以满足 T3 显式契约,回滚一并 DROP)。
--  [建] vkpi_kol_video_evidence(kol_pool_id):现存 idx_evidence_kol_pool(kol_pool_id) 已覆盖。
--       同上以 idx_iso_* 独立命名声明隔离读路径,IF NOT EXISTS 幂等无害。

CREATE INDEX IF NOT EXISTS idx_iso_kol_pool_favorites_staff_pool
  ON vkpi_kol_pool_favorites (staff_id, kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_iso_project_kol_assignments_project_pool
  ON vkpi_project_kol_assignments (project_id, kol_pool_id);

CREATE INDEX IF NOT EXISTS idx_iso_kol_video_evidence_pool
  ON vkpi_kol_video_evidence (kol_pool_id);

COMMENT ON INDEX idx_iso_kol_pool_favorites_staff_pool IS
  'P1 隔离: 某 staff 收藏 kol 集合(staff_id 等值 + 覆盖列 kol_pool_id)。回滚:DROP INDEX IF EXISTS idx_iso_kol_pool_favorites_staff_pool;';
COMMENT ON INDEX idx_iso_project_kol_assignments_project_pool IS
  'P1 隔离: 项目内 kol 指派(project_id, kol_pool_id)连接路径。回滚:DROP INDEX IF EXISTS idx_iso_project_kol_assignments_project_pool;';
COMMENT ON INDEX idx_iso_kol_video_evidence_pool IS
  'P1 隔离: 按 kol_pool_id 连接视频证据。回滚:DROP INDEX IF EXISTS idx_iso_kol_video_evidence_pool;';
