-- 158 官号视频画面质量分析(fit-safe 独立管线)—— 为 18 自家官号的视频跑 Gemini final_v1,
-- 取 content_quality_score 等画质分,供「每日官号报告」③画面质量读真分。
-- 红线(关键):**完全不进 kol_pool / 不建 evidence / 不触 viltrox_fit_score / 不动 fit 指纹**。
-- 官号视频按 (channel_id, post_uid) 独立存本表,与外部 KOL 的评分域物理隔离。
-- LLM/Gemini 走预算闸 cron:official_visual。回滚见 158_..._down.sql。

CREATE TABLE IF NOT EXISTS vkpi_official_post_visual (
    channel_id            BIGINT      NOT NULL,
    post_uid              TEXT        NOT NULL,
    platform              TEXT,
    post_url              TEXT,
    title                 TEXT,
    status                TEXT        NOT NULL DEFAULT 'pending',  -- pending/analyzing/ready/skipped/failed
    content_quality_score NUMERIC(6,2),
    viewer_heart_score    NUMERIC(6,2),
    product_proof_score   NUMERIC(6,2),
    visual_summary        TEXT,
    scores_json           TEXT,
    model                 TEXT,
    error                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (channel_id, post_uid)
);

COMMENT ON TABLE vkpi_official_post_visual IS
    '官号视频画面质量(Gemini final_v1 content_quality_score 等);fit-safe 独立表,不进 kol_pool/不触 viltrox_fit_score。';

CREATE INDEX IF NOT EXISTS idx_vkpi_official_post_visual_channel
    ON vkpi_official_post_visual (channel_id, status);

-- 预算闸:官号画质分析(每号 5-8 条,18 号一轮约百条,逐条 Gemini)。cap $8 留余量;超限跳过保留已分析。
INSERT INTO vkpi_provider_budget_caps
    (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
VALUES
    ('cron:official_visual', 8.00, 0, 6.40, 8.00, NULL, 'skip_llm_keep_last',
     '{"seeded_by":"migration_158","tier":"cron","package":"official_visual","provider":"gemini"}')
ON CONFLICT (scope) DO NOTHING;

-- 调度任务(增量处理器,每轮处理少量待分析视频,默认开)。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_official_visual_scan', '官号视频画质分析(增量·Gemini·预算闸$8)', TRUE, 'high')
ON CONFLICT (task_key) DO NOTHING;
