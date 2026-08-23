-- 294: 调度台账去假绿(2026-08-23 prod 只读体检,波 D·S2)。
-- 1) vkpi_scheduler_fire_claims.status 放行两类新终态:
--    - 'claim_failed':claim 阶段(执行锁建连 / 连接池取连接 PoolTimeout / INSERT)就炸,任务体根本没跑,
--      此前台账连 running 行都没有(偶数小时 :50:47 二十多任务同秒起跑撞池超时,5 次/日无迹可寻);
--    - 'blocked:<key>':任务体被前置闸挡住没真跑(config-gate 拒跑 → blocked:gate_disabled;
--      readiness 未就绪 → blocked:memory_not_ready),此前一律记 completed。
--    迁移 249 的 CHECK 是内联匿名约束(自动名 vkpi_scheduler_fire_claims_status_check),这里改成命名约束。
-- 2) scheduler_tasks.last_status(ok|failed|blocked):此前只有 last_error 文本,"挡住没跑"与"跑成"无法区分。
-- 仅 additive;回滚见 294_vkpi_scheduler_fire_ledger_honesty_down.sql。
-- The migration runner owns the surrounding transaction and advisory lock. Do not add BEGIN/COMMIT here.

ALTER TABLE vkpi_scheduler_fire_claims DROP CONSTRAINT IF EXISTS vkpi_scheduler_fire_claims_status_check;
ALTER TABLE vkpi_scheduler_fire_claims DROP CONSTRAINT IF EXISTS chk_vkpi_scheduler_fire_claims_status;
ALTER TABLE vkpi_scheduler_fire_claims ADD CONSTRAINT chk_vkpi_scheduler_fire_claims_status
  CHECK (status IN ('running', 'completed', 'failed', 'claim_failed') OR status LIKE 'blocked:%');

ALTER TABLE scheduler_tasks ADD COLUMN IF NOT EXISTS last_status TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN vkpi_scheduler_fire_claims.status IS
    'running|completed|failed|claim_failed|blocked:<key>;claim_failed=claim 阶段就炸没跑,blocked:*=前置闸挡住没真跑(非假绿)。';
COMMENT ON COLUMN scheduler_tasks.last_status IS
    '最近一次运行结论 ok|failed|blocked(blocked=任务已启用但前置闸挡住没真跑;gate 拒跑不写本行)。';
