-- 209_vkpi_channel_assignments.sql — 官方账号分配表(C7 矩阵二期 A3)。
-- 背景:官方账号矩阵(18 官号)缺「哪个成员负责哪个账号」的分配管理;本表补上这层关系。
-- 口径:channel_id 指 vkpi_employee_channels.id;staff_id 指 staff.id;不建 FK,与历史数据解耦。
-- (channel_id, role) 唯一,role 默认 owner=主负责人,预留 backup 等值;assigned_at 存 UTC ISO 文本,
-- 与 channels 域其余时间列口径一致。
-- additive、幂等(IF NOT EXISTS),可整表回滚;注释零 ASCII 问号。
-- 红线:纯分配关系表,绝不触 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_channel_assignments (
    id BIGSERIAL PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    staff_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',
    assigned_at TEXT,
    assigned_by_staff_id INTEGER,
    UNIQUE (channel_id, role)
);
CREATE INDEX IF NOT EXISTS idx_vkpi_channel_assignments_staff
    ON vkpi_channel_assignments(staff_id);
COMMIT;
