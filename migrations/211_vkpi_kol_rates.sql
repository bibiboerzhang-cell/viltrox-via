-- 211_vkpi_kol_rates.sql — KOL 报价卡 RateCard v0 报价台账表(件B)。
-- 背景:全图唯一新数据缺口是 KOL 报价。本表存人工录入的真实报价(合同价/外联回复价/谈判价),
--   estimate_rate 有真报价行时取中位数出估价区间(confidence 升),
--   无行时走模块内 CPM 行业基准兜底(method=cpm_benchmark_v0, confidence=low)。
-- source 取值:contract(合同成交价) / outreach_reply(外联回复开价) / negotiation(谈判过程报价)
--   / cpm_benchmark(基准折算存档,不参与真报价中位数)。
-- confidence 取值:low / medium / high(录入时按来源可信度给,缺省按 source 推默认)。
-- kol_pool_id 口径:vkpi_kol_pool.id,仅做归属线索,不做外键强约束(避免历史脏数据 apply 失败)。
-- additive、幂等(IF NOT EXISTS);注释零 ASCII 问号、零百分号(避 compat 占位符炸 apply 的陷阱)。
-- 红线:纯报价台账表,绝不触 viltrox_fit_score、不碰 rule_v0 打分逻辑。
BEGIN;
CREATE TABLE IF NOT EXISTS vkpi_kol_rates (
    id BIGSERIAL PRIMARY KEY,
    kol_pool_id BIGINT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    amount_usd NUMERIC(12,2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL DEFAULT 'negotiation',
    confidence TEXT NOT NULL DEFAULT 'medium',
    note TEXT DEFAULT '',
    effective_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- content_type 建议取值:dedicated_video(定制专片) / integrated_mention(植入口播) /
--   short_video(短视频) / post(图文帖) / other;不做 CHECK 硬约束,口径由应用层收敛。
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_rates_kol
    ON vkpi_kol_rates(kol_pool_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_kol_rates_source
    ON vkpi_kol_rates(source, created_at DESC);
COMMIT;
