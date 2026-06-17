-- 160: 给 vkpi_project_members 加一列 shared_via_group_id(标记「由某分组共享产生」的行)。
-- 背景(P-GROUP-34):此前 vkpi_staff_groups.permissions_json.shared_projects 只是设置页文本
--   元数据,没人把它变成真访问 —— 分组共享是「假授权」。本列让 staff_groups/service.py 把
--   「组级 shared_projects × 组成员」展开成真 vkpi_project_members 行,scope.project_filter /
--   assert_project_access 据此放行(零改 scope.py,纯复用现成 enforce)。
-- shared_via_group_id 用途:区分「由分组共享产生」的行(改组成员/改共享项目即重算、可幂等重建)
--   与 P-COLLAB-1 的「手动逐个共享」行(shared_via_group_id IS NULL)。重算逻辑只 DELETE
--   本组(shared_via_group_id = <gid>)产生的行,绝不误删手动共享行。
-- 红线(ADDITIVE-only):只新增一列 + 一个部分索引,绝不改 vkpi_projects/staff 任何列,
--   绝不动既有 own-only 限制 —— 分组共享只「加宽」,不「放开」;restricted 项目仍只认
--   assigned/creator/can_view_all(放行 gate 仍在 scope.py 的非 restricted 子句上)。
-- 注意:本迁移不在 connection.py 自动注册(由主控审阅后手动接线 "160_vkpi_project_members_group_origin.sql"),回滚见 _down。

-- 注:vkpi_staff_groups.id 是 VARCHAR(64)=grp_<ms 毫秒时间戳>,其数字后缀是 13 位毫秒值
--   (~1.7e12),超出 INTEGER(上限 ~2.1e9)。故本列用 BIGINT(而非任务字面的 INTEGER)
--   以容纳组 id 的毫秒后缀,避免 service.py 展开时溢出/截断。NULL = 非分组来源(手动共享)。
ALTER TABLE vkpi_project_members
    ADD COLUMN IF NOT EXISTS shared_via_group_id BIGINT;

-- 部分索引:按 group 重算时 DELETE ... WHERE shared_via_group_id = <gid> 走索引(只覆盖组共享行)。
CREATE INDEX IF NOT EXISTS idx_vkpi_project_members_group_origin
    ON vkpi_project_members (shared_via_group_id)
    WHERE shared_via_group_id IS NOT NULL;

COMMENT ON COLUMN vkpi_project_members.shared_via_group_id IS
    '非 NULL = 该共享行由 vkpi_staff_groups(此 id 的分组)的组级 shared_projects 展开产生,改组即重算(幂等);NULL = P-COLLAB-1 手动逐个共享行。重算只 DELETE 本组行,不碰手动行。ADDITIVE——只加宽分组共享可见性,不收窄既有 own-only。';
