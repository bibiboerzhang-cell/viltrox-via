-- 172_scheduler_task_content_fit_batch.sql — 给「content_fit 过夜批量(Anthropic Batch · 省 50% LLM)」
-- 登记 config-gate 开关。job_vkpi_content_fit_batch_refresh 已在 scheduler 注册(02:30 cron),但 gate
-- vkpi_content_fit_batch 此前未种子 → _scheduler_task_enabled 恒 False → 永不提交。种这一条后,任务即在
-- 「设置 > 定时任务」可见 + 可开关。默认 OFF(运营显式开启才真提交批量);常开的 llm_batch_poll 回收
-- 轮询不受此 gate(空跑无害)。幂等:ON CONFLICT DO NOTHING。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_content_fit_batch', '内容契合过夜批量刷新(Anthropic Batch · 省 50% LLM)', 'medium')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
