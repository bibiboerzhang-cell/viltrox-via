-- 167_scheduler_task_voc.sql — 给「评论情感每日刷新(VoC)」登记 config-gate 开关。
-- 默认 OFF(scheduler_tasks.enabled 列默认 FALSE);运营在 Ops 页显式开启才真跑。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(可重跑,绝不覆盖运营已调过的开关)。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_comment_sentiment_refresh', '评论情感每日刷新(VoC)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
