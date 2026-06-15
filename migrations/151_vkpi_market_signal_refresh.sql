-- 151 Signals & Alerts 每日刷新 —— 注册外部信号抓取调度任务。
-- 抓取 allowlisted Google News/Reddit(有界、短超时、零 LLM、零 DB 写),只写 runtime/ops artifact 供展示。
-- 竞品入库仍走人工审核闸(本 job 不 promote)。用户 2026-06-15 授权每天自动 → enabled=TRUE。
-- risk_level=medium(有外部 HTTP,但 allowlisted+有界,无 LLM/无写库)。回滚见 _down.sql。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_market_signal_refresh', 'Signals & Alerts 每日刷新(竞品/Reddit 热度·有界·无LLM)', TRUE, 'medium')
ON CONFLICT (task_key) DO NOTHING;
