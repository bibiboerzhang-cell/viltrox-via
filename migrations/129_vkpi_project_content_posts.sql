-- 129: 履约「内容帖子候选」表(additive,零自动裁决)。
-- 观察窗口(128)内扫描到的疑似内容(短视频/帖子)以「候选」身份入本表,等人复核确认。
-- 红线:本表 + 128(observation_windows)是仅有的两个写入目标。系统只 CREATE 候选 + 读,
--   绝不自动改 vkpi_projects.stage/closed_at、vkpi_project_kol_assignments.stage、
--   vkpi_cost_ledger、viltrox_fit_score、rule_v0。复核(matched/rejected/needs_review)
--   只动本表行的 status,绝不连带改业务表。
-- status:candidate(候选,待人看)| matched(人确认匹配)| rejected(人否决)|
--   needs_review(需进一步人工)| retrospective_ready(人确认后可进复盘聚合)。这些都是「展示/复核态」,非自动裁决。
-- match_confidence/match_reason/matched_terms 仅供人参考,绝不据此自动改任何业务状态。
-- 去重:unique(project_id, content_url) — 同项目同 URL 只一条;代码侧另有先查后插兜底。
-- 当前无 delivered shipment → 无窗口 → 无候选(诚实)。
-- 回滚见 129_vkpi_project_content_posts_down.sql(DROP TABLE)。

CREATE TABLE IF NOT EXISTS vkpi_project_content_posts (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES vkpi_projects(id) ON DELETE CASCADE,
    assignment_id BIGINT,
    kol_pool_id BIGINT,
    evidence_id BIGINT,
    platform TEXT,
    content_url TEXT,
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    published_at TIMESTAMPTZ,
    view_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT NOT NULL DEFAULT 0,
    comment_count BIGINT NOT NULL DEFAULT 0,
    matched_terms TEXT NOT NULL DEFAULT '',
    match_confidence NUMERIC(4, 3) NOT NULL DEFAULT 0,
    match_reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vkpi_pcp_project_status
    ON vkpi_project_content_posts (project_id, status);
CREATE INDEX IF NOT EXISTS idx_vkpi_pcp_assignment
    ON vkpi_project_content_posts (assignment_id);

-- 同项目同 URL 唯一,避免扫描重复入候选(代码侧另有先查后插兜底)。
CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_pcp_project_url
    ON vkpi_project_content_posts (project_id, content_url);

COMMENT ON TABLE vkpi_project_content_posts IS
    '履约内容帖子候选:观察窗口内扫到的疑似内容以候选入库供人复核;系统只 CREATE 候选 + 读,零自动裁决,绝不改项目/派单/费用状态。';
