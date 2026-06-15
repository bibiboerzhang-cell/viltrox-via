-- 147: KOL 自助门户专属访问 token(只读·token 隔离)。
-- 每个 token 绑定一个 vkpi_kol_pool.id;门户公开端点凭 token 解析出 kol_pool_id,
-- 只聚合该 KOL 自己的 点击/订单/GMV/被分配项目,绝不泄露别人。
-- token 随机不可猜(secrets.token_urlsafe),UNIQUE 保唯一;revoked 软撤销(只读门户立即失效)。
-- 与 KOL Pool / 评分域物理隔离;绝不碰 viltrox_fit_score / rule_v0。
-- 审计列 created_by_staff_id 记发链人(红线:新表带审计列)。

CREATE TABLE IF NOT EXISTS vkpi_kol_portal_tokens (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,  -- 审计:发链人
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (token <> '')                                                  -- 兜底:空 token 不可入库
);

-- 每个 KOL 至多一个未撤销的 live token → issue_token 幂等复用在 DB 层有硬保证(防并发竞态)。
CREATE UNIQUE INDEX IF NOT EXISTS idx_vkpi_kol_portal_tokens_live
    ON vkpi_kol_portal_tokens(kol_pool_id)
    WHERE revoked = FALSE;

COMMENT ON TABLE vkpi_kol_portal_tokens IS 'V-KPI KOL self-service portal access tokens (read-only, token-isolated). One live token per kol_pool_id (partial unique index); revoked=TRUE invalidates immediately. token is random/unguessable.';
