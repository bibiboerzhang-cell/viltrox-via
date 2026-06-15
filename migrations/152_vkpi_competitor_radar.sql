-- 152 竞品新品雷达(Gemini+Google 接地)—— 每早实时查海外竞品新镜头/相机发布 + 对 Viltrox 影响。
-- 红线:LLM 走预算闸(cron:competitor_radar 硬上限 $1/日)+ 代理;只写本表,不碰 fit_score/指纹。
CREATE TABLE IF NOT EXISTS vkpi_competitor_radar (
    snapshot_date DATE        PRIMARY KEY,
    content_json  TEXT        NOT NULL,
    model         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE vkpi_competitor_radar IS '竞品新品雷达:每日 Gemini 接地查海外竞品动态(只读展示;预算闸硬限)。';

INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('cron:competitor_radar', 1.00, 0, 0.80, 1.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_152","tier":"cron","package":"competitor_radar","provider":"gemini"}')
ON CONFLICT (scope) DO NOTHING;

INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_competitor_radar', '竞品新品雷达(每早·Gemini接地·预算闸$1)', TRUE, 'high')
ON CONFLICT (task_key) DO NOTHING;
