-- 153 报告深度分析(LLM)—— 「生成报告」按钮点「深度分析」时,把拼好的全量真实数据喂 LLM,
--     整理成给管理层看的经营分析(执行摘要/亮点/风险/建议/市场洞察)。按需触发,非定时,故无 scheduler_task。
-- 红线:LLM 走预算闸(dashboard:report_analysis 硬上限 $3/日)+ Claude/Gemini 自带代理;
--       同内容当天缓存复用省预算;只写本表,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹 / rule_v0。
-- 回滚见 153_vkpi_report_analysis_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_report_analysis (
    id            BIGSERIAL   PRIMARY KEY,
    content_hash  TEXT        NOT NULL,
    period        TEXT,
    language      TEXT,
    analysis_json TEXT        NOT NULL,
    model         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_report_analysis_hash
    ON vkpi_report_analysis (content_hash, created_at DESC);

COMMENT ON TABLE vkpi_report_analysis IS
    '报告深度分析:按需 LLM 据报告全量数据生成的经营分析(缓存 + 留痕;预算闸硬限)。';

-- 预算闸:按需触发(用户点按钮),给一天 $3 余量(单次约 $0.10,缓存命中不计);超限 → 回退不调用。
INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('dashboard:report_analysis', 3.00, 0, 2.40, 3.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_153","tier":"on_demand","package":"report_analysis","provider":"claude"}')
ON CONFLICT (scope) DO NOTHING;
