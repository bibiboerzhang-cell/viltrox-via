-- 128: 履约「观察窗口」表(additive,零自动裁决)。
-- 物流签收(vkpi_shipments.delivered_at)落地后,人/扫描器为该派单开一个观察窗口:
--   starts_at = delivered + 7d、ends_at = delivered + 45d,等内容(短视频/帖子)出现供人核对。
-- 红线:本表 + 129(content_posts)是仅有的两个写入目标。系统只 CREATE「待人看」的窗口/候选,
--   绝不自动改 vkpi_projects.stage/closed_at、vkpi_project_kol_assignments.stage、
--   vkpi_cost_ledger、viltrox_fit_score、rule_v0。窗口的 status 流转只动本表行,不连带改业务表。
-- status:pending(待扫描)| scanning(扫描中)| matched(已匹配内容,待人确认)|
--   content_missing(窗口到期无内容)| closed(已关闭/人工收尾)。这些都是「展示态」,非业务裁决。
-- 去重:同 (project_id, assignment_id, kol_pool_id) 的活动窗口(pending/scanning/matched)由代码侧
--   先查后插兜底;NULL 维度用 IS NULL 比较。
-- 当前 vkpi_shipments 多无 delivered_at 数据 → 扫描产出为空(诚实反映物流断流)。
-- 回滚见 128_vkpi_project_content_observation_windows_down.sql(DROP TABLE)。

CREATE TABLE IF NOT EXISTS vkpi_project_content_observation_windows (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    assignment_id BIGINT,
    kol_pool_id BIGINT,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending',
    scan_count INT NOT NULL DEFAULT 0,
    last_scan_at TIMESTAMPTZ,
    matched_content_post_id BIGINT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_pcow_project
    ON vkpi_project_content_observation_windows (project_id);
CREATE INDEX IF NOT EXISTS idx_vkpi_pcow_status_ends
    ON vkpi_project_content_observation_windows (status, ends_at);

COMMENT ON TABLE vkpi_project_content_observation_windows IS
    '履约观察窗口:物流签收后为派单开「等内容」窗口供人核对;系统只 CREATE 窗口 + 读,零自动裁决,绝不改项目/派单/费用状态。';
