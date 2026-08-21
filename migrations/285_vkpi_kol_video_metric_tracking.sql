-- Explicit durable subscriptions for automatic KOL video metric refresh.
--
-- The scheduler task is registered OFF.  Enabling it only permits a bounded
-- enqueue scan; provider execution remains inside the existing apify_jobs
-- budget, replay and execution-claim fences.

CREATE TABLE IF NOT EXISTS vkpi_kol_video_metric_tracking (
    evidence_id BIGINT PRIMARY KEY
        REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
    tracked_by_staff_id BIGINT
        REFERENCES staff(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'my_kol_video_tracking',
    last_enqueued_at TIMESTAMPTZ,
    last_job_id BIGINT REFERENCES apify_jobs(id) ON DELETE SET NULL,
    last_enqueue_status TEXT NOT NULL DEFAULT '',
    pause_reason TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_vkpi_kol_video_metric_tracking_status
        CHECK (status IN ('active', 'paused')),
    CONSTRAINT chk_vkpi_kol_video_metric_tracking_enqueue_status
        CHECK (last_enqueue_status IN ('', 'queued', 'already_queued'))
);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_video_metric_tracking_due
    ON vkpi_kol_video_metric_tracking(status, last_enqueued_at, evidence_id);

CREATE INDEX IF NOT EXISTS idx_vkpi_kol_video_metric_tracking_staff
    ON vkpi_kol_video_metric_tracking(tracked_by_staff_id, status);

-- A manual product link created by the MY KOL tracking endpoint is explicit
-- pre-migration tracking evidence.  Do not infer subscriptions from generic
-- evidence rows or historical provider jobs.
INSERT INTO vkpi_kol_video_metric_tracking (
    evidence_id,
    tracked_by_staff_id,
    status,
    source,
    created_at,
    updated_at
)
SELECT DISTINCT ON (l.evidence_id)
    l.evidence_id,
    l.created_by_staff_id,
    'active',
    'migration_285_manual_product_link',
    l.created_at,
    NOW()
FROM vkpi_kol_video_product_links l
WHERE l.relation_type='manual'
  AND l.source='my_kol_video_tracking'
  AND l.created_by_staff_id IS NOT NULL
ORDER BY l.evidence_id, l.created_at DESC, l.id DESC
ON CONFLICT (evidence_id) DO NOTHING;

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
    'vkpi_kol_video_metric_refresh',
    'Tracked KOL video metric refresh enqueue scan',
    FALSE,
    24,
    0,
    '00:00-23:59 UTC',
    'marketing_ops',
    'high'
)
ON CONFLICT (task_key) DO NOTHING;

COMMENT ON TABLE vkpi_kol_video_metric_tracking IS
    'Explicit tracked-video subscriptions; scheduler queues only and never calls providers';
