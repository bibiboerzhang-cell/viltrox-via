-- P13 分享/撤销审计台账:谁(actor)对哪个 project/event 把谁(member)加为什么角色,
-- 何时撤销。成员表(131/132)只记 added_by/created_at(加可溯),撤销=硬 DELETE 无留痕;
-- 这张不可变台账补齐"谁撤销、何时"的审计,多人协作后可追责。软引用 target_id(无 FK),
-- 故成员行被删后审计史仍在。
CREATE TABLE IF NOT EXISTS vkpi_share_audit (
    id BIGSERIAL PRIMARY KEY,
    target_type TEXT NOT NULL,        -- 'project' | 'event'
    target_id TEXT NOT NULL,          -- project id(文本)或 event_id(evt_...)
    action TEXT NOT NULL,             -- 'share' | 'revoke' | 'role_change'
    member_staff_id BIGINT,           -- 被分享/被撤销的成员
    role TEXT,                        -- viewer | editor(share/role_change 时)
    actor_staff_id BIGINT,            -- 操作人(谁分享/谁撤销)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_share_audit_target ON vkpi_share_audit(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_share_audit_actor ON vkpi_share_audit(actor_staff_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_share_audit_created ON vkpi_share_audit(created_at);
