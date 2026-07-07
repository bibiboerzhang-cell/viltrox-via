-- 218_scheduler_task_gtm_loop.sql — GTM 裁决闭环:注册两条每日 cron 闸门(默认 OFF)。
-- job_vkpi_gtm_spawn_verdicts:每日扫到期未裁决 bet,生成 gtm_verdict 置顶裁决任务
-- (幂等,同 dedupe_key 已存在即跳过;只生成待人裁决任务,零自动裁决)。
-- job_vkpi_gtm_windows_refresh:对未裁决 gtm_outcomes 行按账龄回填 7/14/28 三窗证据
-- (已裁决行冻结不刷,只写 window_ 三列,绝不触 decision)。
-- 幂等:ON CONFLICT(task_key) DO NOTHING(沿用 188 同款)。默认 OFF,运营在 Ops 页显式开。
-- 零触 viltrox_fit_score。回滚见 218_scheduler_task_gtm_loop_down.sql。
BEGIN;
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('vkpi_gtm_spawn_verdicts', 'GTM到期押注裁决任务生成(每日,幂等,零自动裁决)', 'low'),
    ('vkpi_gtm_windows_refresh', 'GTM结果三窗证据回填(每日,7/14/28,已裁决冻结)', 'low')
ON CONFLICT (task_key) DO NOTHING;
COMMIT;
