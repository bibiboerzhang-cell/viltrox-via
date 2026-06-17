-- 159: 共享 KOL 池(P-GROUP-7)—— "把我收藏的 My KOL 显式共享给某个成员"。
-- 语义边界(镜像 107 收藏表 + vkpi_project_members 共享表):
--   收藏(vkpi_kol_pool_favorites) = staff 个人的 My KOL 归宿(私有);
--   本表(vkpi_kol_pool_members)  = "kol_pool_id 被显式共享给 staff_id" 的只读授予,
--   让被共享成员的 My KOL 视图里也能看见这条 KOL(只读可见,绝不改归属/收藏/认领)。
-- kol_pool_id 指向 vkpi_kol_pool(id)(BIGSERIAL,与收藏表同主键),ON DELETE CASCADE。
-- staff_id  = 被共享的接收方;shared_by = 发起共享的人(留痕,可空容旧)。
-- UNIQUE(kol_pool_id, staff_id):同一 KOL 对同一成员只共享一次,重复=幂等。
-- 红线:全新隔离表,既有 vkpi_kol_pool / favorites / claims 零改动;
--       绝不碰 viltrox_fit_score / rule_v0;占位符 '?'(compat 由方言层翻译)。
-- 回滚见 159_vkpi_kol_pool_members_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_kol_pool_members (
    id          BIGSERIAL   PRIMARY KEY,
    kol_pool_id BIGINT      NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    staff_id    BIGINT      NOT NULL REFERENCES staff(id),
    shared_by   BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (kol_pool_id, staff_id)
);

-- 镜像 member_shared_project_ids 的查询形态:WHERE staff_id=? → kol_pool_id 列表。
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_members_staff
    ON vkpi_kol_pool_members(staff_id, created_at DESC);
-- 反向:某 KOL 被共享给了谁(共享弹窗"当前成员"列表 + 删除)。
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_pool_members_pool
    ON vkpi_kol_pool_members(kol_pool_id);

COMMENT ON TABLE vkpi_kol_pool_members IS
    '共享 KOL 池:把某 kol_pool_id 显式共享给某 staff_id(只读可见,不改归属/收藏/认领)。';
