-- 157 每日官方账号分析报告 —— 为 18 个自家官号(vkpi_employee_channels)每天 2 轮生成
-- 「播放/评论/画面质量/数据趋势 + 提升建议」LLM 报告。
-- 红线:这是「我方官号绩效报告」,数据源 vkpi_employee_channels/vkpi_channel_metrics/
--       vkpi_channel_post_metrics/vkpi_comments,与外部 KOL 的 viltrox_fit_score/rule_v0/
--       kol_pool 物理两套,绝不读写后者。LLM 全走预算闸 cron:official_daily_report。
-- 回滚见 157_vkpi_official_account_daily_report_down.sql。

-- 一账号一天一轮一份(round_key 区分中国早8/美西早6 两轮,各保留各自市场窗口的新鲜数据)。
CREATE TABLE IF NOT EXISTS vkpi_official_account_daily_report (
    channel_id   BIGINT      NOT NULL,
    report_date  DATE        NOT NULL,
    round_key    TEXT        NOT NULL DEFAULT 'daily',
    platform     TEXT,
    handle       TEXT,
    report_json  TEXT        NOT NULL,
    model        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (channel_id, report_date, round_key)
);

COMMENT ON TABLE vkpi_official_account_daily_report IS
    '每日官号分析报告:每号每天每轮一份 LLM 报告(播放/评论/画质/趋势/建议);只读展示,预算闸硬限。';

CREATE INDEX IF NOT EXISTS idx_vkpi_official_daily_report_recent
    ON vkpi_official_account_daily_report (report_date DESC, channel_id);

-- 预算闸:每天 2 轮 × 18 号 × ~$0.10 ≈ $3.6/日,cap 设 $4.00 留余量;超限 → 跳过 LLM 保留上一份。
INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('cron:official_daily_report', 4.00, 0, 3.20, 4.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_157","tier":"cron","package":"official_daily_report","provider":"claude"}')
ON CONFLICT (scope) DO NOTHING;

-- 调度任务:用户 2026-06-16 授权自动(每天 2 轮·中国早8/美西早6)。risk=high(LLM 类),预算闸是硬安全网。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_official_daily_report', '每日官号分析报告(每天2轮·中国早8/美西早6·LLM·预算闸$4)', TRUE, 'high')
ON CONFLICT (task_key) DO NOTHING;
