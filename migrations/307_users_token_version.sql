-- 307: users 加 token_version —— 登录令牌的服务端吊销版本号(公测阻断项 S-02)。
-- 背景:JWT 无状态,此前登出只清 cookie、改密不失效旧令牌,7 天内泄露的令牌无法撤回。
-- 口径:签发时把本列写进 JWT 载荷 tv;校验时(core/security.get_current_user)与本列比对,
--   不等即拒(401)。改密 / 重置密码 / 登出 / 管理员踢人 → 本列 +1,该用户此前签发的
--   全部令牌立即失效(经 30 秒认证缓存;写点会主动清缓存,同进程立即生效)。
-- 允许 NULL、无默认:NULL 等价 0(「从未吊销过」),读端一律 COALESCE(token_version, 0);
--   旧令牌载荷缺 tv 也按 0 处理,故迁移当刻不会把任何在线用户踢下线。
--   保持 additive-nullable-defaultless 形态,便于回滚兼容(旧代码不读此列)。
-- 唯一写点:backend/app/services/auth/token_revocation.py(revoke_user_sessions)。
-- 红线:只加列,不碰 vkpi_kol_pool 与任何打分列。回滚见 307_users_token_version_down.sql。
ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NULL;

COMMENT ON COLUMN users.token_version IS
    '登录令牌吊销版本号:JWT 载荷 tv 必须等于本列(NULL 等价 0)才通过校验;改密 / 重置密码 / 登出 / 管理员踢人时 +1,一次让该用户全部既有令牌失效;唯一写点 services/auth/token_revocation.py。NULL means never revoked, not missing data.';
