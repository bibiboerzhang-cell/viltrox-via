-- 150 AI Today 今日热点(LLM)—— 每早 8点中国时区据真实行业热点生成拍摄方案+话题。
-- 红线:LLM 走预算闸(cron:ai_today_hot 硬上限 $1.00/日)+ claude client 代理;一天一次;
--       只写本表,绝不碰 vkpi_kol_pool / viltrox_fit_score / 指纹。
-- 回滚见 150_vkpi_ai_today_hot_down.sql。

CREATE TABLE IF NOT EXISTS vkpi_ai_today_hot (
    snapshot_date DATE        PRIMARY KEY,
    content_json  TEXT        NOT NULL,
    model         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE vkpi_ai_today_hot IS
    'AI Today 今日热点:每日 LLM 生成的拍摄方案/话题/重点决策(只读展示;预算闸硬限)。';

-- 预算闸:单日单次,硬上限 $1.00(一次小调用约 $0.05,留足余量)。超限 → 回退不调用。
INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('cron:ai_today_hot', 1.00, 0, 0.80, 1.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_150","tier":"cron","package":"ai_today","provider":"claude"}')
ON CONFLICT (scope) DO NOTHING;

-- 调度任务:用户 2026-06-15 明确授权每天自动。risk_level=high(LLM 类),但预算闸是硬安全网。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_ai_today_hot', 'AI Today 今日热点(每早8点·LLM·预算闸$1)', TRUE, 'high')
ON CONFLICT (task_key) DO NOTHING;
