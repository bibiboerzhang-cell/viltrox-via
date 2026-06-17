-- 160 回滚:删除 shared_via_group_id 列及其部分索引。
-- 本列为隔离标记列,vkpi_projects / staff 不受影响;回滚只去掉「分组共享重算」的来源标记,
--   既有手动共享行(shared_via_group_id IS NULL 的那些)在删列后仍是普通共享行,own-only 不动。
-- 注意:回滚后由分组展开产生的共享行将失去「组来源」标记 —— 若需彻底清理,主控可在删列前
--   先 DELETE FROM vkpi_project_members WHERE shared_via_group_id IS NOT NULL(非本迁移职责)。
DROP INDEX IF EXISTS idx_vkpi_project_members_group_origin;
ALTER TABLE vkpi_project_members
    DROP COLUMN IF EXISTS shared_via_group_id;
