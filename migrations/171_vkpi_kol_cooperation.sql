-- 171(P1-1 KOL 合作动作 · 平台为基准):KOL 合作状态时间线(append-only)。
-- 口径(用户):合作状态以「我们平台」为基准源,按真 kol_pool_id 落库,数据互传方便;
-- 当前合作状态 = 该 KOL 最新事件的 status_after。4 个动作(续约/加大投入/退出合作/评估)各记
-- 一条事件 + 操作人 + 备注。surface 在真 KOL Pool 详情(非 dashboard mover 预览弹窗)。
-- additive/隔离:与 KOL 评分域物理隔离;无 viltrox_fit_score / rule_v0 触点。

CREATE TABLE IF NOT EXISTS vkpi_kol_cooperation_events (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,                  -- renew | scale_up | exit | evaluate | note
    status_after TEXT NOT NULL DEFAULT '',      -- active | scaling | exited | evaluating | ''
    note TEXT DEFAULT '',
    actor_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_cooperation_kol ON vkpi_kol_cooperation_events(kol_pool_id, created_at DESC);

COMMENT ON TABLE vkpi_kol_cooperation_events IS 'KOL 合作动作时间线(平台为基准;续约/加大投入/退出/评估;当前状态=最新 status_after)';
