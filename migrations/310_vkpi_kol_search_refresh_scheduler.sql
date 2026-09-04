-- Wire the existing KOL profile incremental-refresh registry row to the
-- bounded daily Smart Search inventory refresher. The initial rollout uses a
-- database slot ledger to hard-cap all concurrent/manual runs at 5 new
-- maintenance jobs per America/New_York day. Five maintenance jobs do not mean
-- five provider calls: one job may perform multiple provider requests, and
-- provider budgets remain a separate worker boundary. Final LLM, contact and
-- derived-profile follow-ups are suppressed and viltrox_fit_score is untouched.
-- Worker/provider budget fences remain authoritative. The migration registers
-- the task OFF; rollout activation is an explicit operator decision.
INSERT INTO scheduler_tasks (
    task_key,
    label,
    enabled,
    max_daily_runs,
    max_daily_cost_cents,
    allowed_hours,
    risk_level
) VALUES (
    'kol_profile_incremental_refresh',
    'KOL 搜索库存每日增量刷新',
    FALSE,
    1,
    0,
    '03:00-06:00 America/New_York',
    'medium'
)
ON CONFLICT (task_key) DO UPDATE SET
    label=EXCLUDED.label,
    -- Intentionally force OFF on upgrade as well as first install. Migration
    -- 130 registered this key as inert metadata; a stale historical toggle
    -- must not silently authorize the newly paid execution chain. Applying or
    -- reapplying 310 may therefore disable this row. An operator must make a
    -- fresh, explicit enable decision after migration and canary approval.
    enabled=FALSE,
    max_daily_runs=EXCLUDED.max_daily_runs,
    max_daily_cost_cents=EXCLUDED.max_daily_cost_cents,
    allowed_hours=EXCLUDED.allowed_hours,
    risk_level=EXCLUDED.risk_level,
    updated_at=NOW();

-- Source-scoped cooldown/daily-cap probes must not scan the whole durable queue.
CREATE INDEX IF NOT EXISTS idx_apify_jobs_kol_search_inventory_source_created
ON apify_jobs ((payload ->> 'kol_pool_id'), created_at DESC)
WHERE job_type='kol_profile_deep_crawl'
  AND payload ->> 'source'='kol_search_inventory_daily';

-- Cost-safety invariant: no process-local lock or APScheduler max_instances
-- assumption is used for the 5-job ceiling. The composite primary key is the
-- cross-process allocator; committed unused slots fail closed until next day.
CREATE TABLE IF NOT EXISTS vkpi_kol_search_inventory_daily_slots (
    batch_date         DATE        NOT NULL,
    slot_no            SMALLINT    NOT NULL CHECK (slot_no BETWEEN 1 AND 5),
    reservation_token  TEXT        NOT NULL,
    job_id              BIGINT      NULL REFERENCES apify_jobs(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (batch_date, slot_no)
);

COMMENT ON TABLE vkpi_kol_search_inventory_daily_slots IS
    'Hard cap allocator: at most 5 new KOL inventory maintenance jobs per America/New_York calendar day; 5 jobs do not equal 5 provider calls.';
