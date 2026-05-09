-- V-KPI Reports / Exports tables (Phase 7)
-- Implements "完整系统" §12 module — let老板每周一收 PDF 周报
-- Depends on: 023_vkpi_core.sql, 024_vkpi_metric_lineage.sql

-- ===========================================================================
-- vkpi_report_runs
-- 一次报告生成的元数据。每次 generate_weekly_report() 写一行。
-- 与 vkpi_metric_runs 关联：报告的数据来源是 metric_lineage snapshot。
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_report_runs (
    id BIGSERIAL PRIMARY KEY,
    report_uid TEXT NOT NULL UNIQUE,
    report_type TEXT NOT NULL,                  -- 'weekly' | 'monthly' | 'staff' | 'project' | 'kol'
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'all',     -- all | staff | project | kol
    scope_id BIGINT,
    metric_run_id BIGINT REFERENCES vkpi_metric_runs(id) ON DELETE SET NULL,
    triggered_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | rendering | ready | failed
    error_message TEXT DEFAULT '',
    summary_text TEXT DEFAULT '',                -- AI-polished executive summary
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_type_time
    ON vkpi_report_runs(report_type, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_period
    ON vkpi_report_runs(period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_status
    ON vkpi_report_runs(status, triggered_at DESC);


-- ===========================================================================
-- vkpi_report_files
-- 一个 report_run 可能产生多个文件（PDF + CSV + XLSX）
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_report_files (
    id BIGSERIAL PRIMARY KEY,
    report_run_id BIGINT NOT NULL REFERENCES vkpi_report_runs(id) ON DELETE CASCADE,
    file_format TEXT NOT NULL,                  -- 'pdf' | 'csv' | 'xlsx' | 'html'
    file_path TEXT NOT NULL,                    -- S3 key OR local path
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    download_url TEXT DEFAULT '',
    download_expires_at TIMESTAMPTZ,            -- presigned URL expiry
    sha256_hex TEXT DEFAULT '',                 -- file integrity
    download_count INTEGER NOT NULL DEFAULT 0,
    last_downloaded_at TIMESTAMPTZ,
    last_downloaded_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_vkpi_report_files_run
    ON vkpi_report_files(report_run_id, file_format);


-- ===========================================================================
-- vkpi_export_jobs
-- 用户主动触发的导出任务（CSV/XLSX from any data slice）
-- 与 report 不同：report 是结构化报告，export 是任意数据切片导出
-- ===========================================================================
CREATE TABLE IF NOT EXISTS vkpi_export_jobs (
    id BIGSERIAL PRIMARY KEY,
    export_uid TEXT NOT NULL UNIQUE,
    requested_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
    export_type TEXT NOT NULL,                  -- 'attribution' | 'cost' | 'kpi_ledger' | 'projects' | 'kols'
    file_format TEXT NOT NULL,                  -- 'csv' | 'xlsx' | 'pdf'
    filters_json TEXT NOT NULL DEFAULT '{}',    -- 用户筛选参数
    status TEXT NOT NULL DEFAULT 'queued',      -- queued | running | ready | failed | expired
    file_path TEXT DEFAULT '',
    download_url TEXT DEFAULT '',
    download_expires_at TIMESTAMPTZ,
    row_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT DEFAULT '',
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ                      -- export auto-expires after 7 days
);
CREATE INDEX IF NOT EXISTS idx_vkpi_export_jobs_staff
    ON vkpi_export_jobs(requested_by_staff_id, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_vkpi_export_jobs_status
    ON vkpi_export_jobs(status, triggered_at DESC);


-- ===========================================================================
-- 视图：最近一份成功的 weekly report
-- 给前端首页 "Latest Weekly Report" widget 使用
-- ===========================================================================
CREATE OR REPLACE VIEW vkpi_latest_weekly_report AS
SELECT
    r.id AS report_run_id,
    r.report_uid,
    r.period_start,
    r.period_end,
    r.summary_text,
    r.triggered_at,
    f.file_path AS pdf_path,
    f.download_url AS pdf_download_url,
    f.file_size_bytes AS pdf_size_bytes
FROM vkpi_report_runs r
LEFT JOIN vkpi_report_files f
    ON f.report_run_id = r.id AND f.file_format = 'pdf'
WHERE r.report_type = 'weekly'
  AND r.status = 'ready'
ORDER BY r.triggered_at DESC
LIMIT 1;
