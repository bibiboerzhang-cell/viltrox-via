-- 130: 调度任务注册表(S1)—— 仅「可见性 + enable 开关」,本期不含任何实际调度执行。
-- 红线:additive、幂等、零触 viltrox_fit_score / rule_v0 / 既有表。
-- 本表只是「注册 + 可见 + 开关」:所有种子行 enabled=FALSE,任何任务都不会自动运行;
-- 执行链(调度循环 / worker 触发)留待后续单独接入,本迁移不创建也不启动任何循环。
-- main.py 的 _trust_scheduler() 已对本表做 table_exists 守卫的只读计数,本迁移落地后它从
-- "not_configured" 变为真实 {total, enabled} —— 仅读,不执行。
-- 回滚见 130_vkpi_scheduler_tasks_down.sql。

CREATE TABLE IF NOT EXISTS scheduler_tasks (
    id                  BIGSERIAL PRIMARY KEY,
    task_key            TEXT        UNIQUE NOT NULL,
    label               TEXT        NOT NULL DEFAULT '',
    enabled             BOOLEAN     NOT NULL DEFAULT FALSE,
    max_daily_runs      INT         NOT NULL DEFAULT 0,
    max_daily_cost_cents INT        NOT NULL DEFAULT 0,
    allowed_hours       TEXT        NOT NULL DEFAULT '',
    owner               TEXT        NOT NULL DEFAULT '',
    risk_level          TEXT        NOT NULL DEFAULT 'low'
                            CHECK (risk_level IN ('low', 'medium', 'high')),
    last_run_at         TIMESTAMPTZ NULL,
    last_success_at     TIMESTAMPTZ NULL,
    last_error          TEXT        NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE scheduler_tasks IS
    'S1 调度任务注册表:仅注册 + 可见 + enable 开关;执行链后续接入。种子行全 enabled=FALSE,无任何自动运行。';
COMMENT ON COLUMN scheduler_tasks.enabled IS
    '是否允许后台调度执行(本期仅作开关标记,后端不据此自动运行任何任务)。';
COMMENT ON COLUMN scheduler_tasks.risk_level IS
    '风险等级 low|medium|high;LLM/批量类默认 high 且 enabled=FALSE。';

-- 种子:全部 enabled=FALSE(默认禁用,不会自动运行);LLM/批量类 risk_level=high。
-- ON CONFLICT(task_key) DO NOTHING 保证迁移可重跑幂等,绝不覆盖运营已调过的开关。
INSERT INTO scheduler_tasks (task_key, label, risk_level) VALUES
    ('task_queue_health',                 '任务队列健康检查',         'low'),
    ('kol_search_session_reconcile',      'KOL 搜索会话对账',         'low'),
    ('project_shipment_sync',             '项目寄件同步',             'low'),
    ('official_account_metrics_sync',     '官方账号指标同步',         'low'),
    ('project_content_observation_scan',  '项目内容观测扫描',         'medium'),
    ('provider_pressure_retry',           'Provider 压力重试',        'medium'),
    ('kol_profile_incremental_refresh',   'KOL 档案增量刷新',         'medium'),
    ('batch_video_analysis',              '批量视频分析(LLM)',       'high'),
    ('deep_result_backfill',              '深度结果回填(LLM/批量)',  'high'),
    ('failed_pool_recycle',               '失败池回收',               'high')
ON CONFLICT (task_key) DO NOTHING;
