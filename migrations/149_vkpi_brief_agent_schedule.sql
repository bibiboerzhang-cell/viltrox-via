-- 149 AI Today 简报 Agent 每日刷新 —— 注册调度任务。
-- 简报 Agent + 上游推荐 Agent 全链确定性:零 LLM、零 provider、零写库(只读 evidence 汇总候选)。
-- 用户 2026-06-15 明确授权「每天自动」→ enabled=TRUE;risk_level=low(零成本)。
-- 仅注册开关,执行链在 services/scheduler/jobs.py;ON CONFLICT DO NOTHING 幂等。
INSERT INTO scheduler_tasks (task_key, label, enabled, risk_level) VALUES
  ('vkpi_brief_agent', 'AI Today 简报 Agent 每日刷新(确定性,无 LLM)', TRUE, 'low')
ON CONFLICT (task_key) DO NOTHING;
