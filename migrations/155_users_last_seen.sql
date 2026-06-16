-- 155 在线状态(presence)—— users 加 last_seen_at,用于团队/切换身份列表的「在线小绿点」。
-- /api/auth/me(前端轮询)节流更新此列;在线 = last_seen_at 在 5 分钟内。additive,零改动既有列。
-- 红线:只加列,不碰 vkpi_kol_pool / viltrox_fit_score / rule_v0。回滚见 155_users_last_seen_down.sql。

ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_users_last_seen_at ON users(last_seen_at);

COMMENT ON COLUMN users.last_seen_at IS '最近活跃时间(/me 心跳节流更新);在线状态判据(5 分钟内=在线)。';
