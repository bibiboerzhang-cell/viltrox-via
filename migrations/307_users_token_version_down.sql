-- 307 down: 回滚 users.token_version。删列即回到「登出只清 cookie、无服务端吊销」的旧行为;
-- 旧代码不读此列,已签发的令牌(含载荷 tv)仍按签名与过期校验,不受影响。
ALTER TABLE users DROP COLUMN IF EXISTS token_version;

DELETE FROM schema_migrations
 WHERE version_key = '307_users_token_version.sql';
