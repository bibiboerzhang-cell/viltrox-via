-- Explicit per-staff KOL content monitoring subscriptions.
--
-- A favorite or share never creates a subscription. The scheduler task is
-- registered OFF and, when explicitly enabled, only queues a bounded recent
-- profile window. Provider execution remains in the existing fenced worker.

CREATE TABLE IF NOT EXISTS vkpi_kol_content_monitoring_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    staff_id BIGINT NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    kol_pool_id BIGINT NOT NULL REFERENCES vkpi_kol_pool(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active',
    cadence_hours INTEGER NOT NULL DEFAULT 24,
    next_due_at TIMESTAMPTZ,
    generation BIGINT NOT NULL DEFAULT 1,
    last_enqueued_at TIMESTAMPTZ,
    last_job_id BIGINT REFERENCES apify_jobs(id) ON DELETE SET NULL,
    last_job_status TEXT NOT NULL DEFAULT '',
    last_success_at TIMESTAMPTZ,
    pause_reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vkpi_kol_content_monitoring_staff_target
        UNIQUE (staff_id, kol_pool_id),
    CONSTRAINT chk_vkpi_kol_content_monitoring_status
        CHECK (status IN ('active', 'paused')),
    CONSTRAINT chk_vkpi_kol_content_monitoring_cadence
        CHECK (cadence_hours BETWEEN 6 AND 168),
    CONSTRAINT chk_vkpi_kol_content_monitoring_generation
        CHECK (generation > 0),
    CONSTRAINT chk_vkpi_kol_content_monitoring_job_status
        CHECK (last_job_status IN (
            '', 'queued', 'already_queued', 'running', 'retrying',
            'done', 'blocked', 'failed', 'cancelled'
        ))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_content_monitoring_due
    ON vkpi_kol_content_monitoring_subscriptions(status, next_due_at, id);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_content_monitoring_target
    ON vkpi_kol_content_monitoring_subscriptions(kol_pool_id, status, updated_at DESC);

INSERT INTO scheduler_tasks (
    task_key,
    label,
    enabled,
    max_daily_runs,
    max_daily_cost_cents,
    allowed_hours,
    owner,
    risk_level
) VALUES (
    'vkpi_kol_content_monitoring',
    'Explicit KOL recent-content monitoring enqueue scan',
    FALSE,
    24,
    0,
    '00:00-23:59 UTC',
    'marketing_ops',
    'high'
)
ON CONFLICT (task_key) DO NOTHING;

-- Retire the legacy favorite/tier/project-derived poll. Its implicit target
-- selection cannot satisfy the per-staff consent fence introduced here.
UPDATE scheduler_tasks
SET enabled=FALSE,
    label='Legacy implicit KOL auto poll (retired; use explicit content monitoring)'
WHERE task_key='kol_auto_poll';

COMMENT ON TABLE vkpi_kol_content_monitoring_subscriptions IS
    'Explicit per-staff KOL recent-content monitoring; never inferred from favorites or shares';
