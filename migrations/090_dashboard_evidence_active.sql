-- 090_dashboard_evidence_active.sql
-- Extend v_dashboard_account_pool with KOL/media evidence fields.
-- Intentionally no BEGIN/COMMIT: this file is safe to dry-run inside an
-- outer transaction with ROLLBACK before formal application.

-- 幂等修复:同 083,先 DROP 再 CREATE(无下游依赖,已核)。此条是 v_dashboard_account_pool 的最终列集(含 evidence 字段)。
DROP VIEW IF EXISTS v_dashboard_account_pool CASCADE;
CREATE VIEW v_dashboard_account_pool AS
WITH latest_official_metric AS (
  SELECT DISTINCT ON (channel_id)
    channel_id,
    followers,
    posts_count,
    total_views,
    total_likes,
    total_comments,
    total_shares,
    engagement_rate,
    captured_at
  FROM vkpi_channel_metrics
  ORDER BY channel_id, snapshot_date DESC, captured_at DESC, id DESC
),
official_accounts AS (
  SELECT
    'official:' || c.id::text AS dashboard_id,
    c.id AS source_id,
    'vkpi_employee_channels'::text AS source_table,
    c.platform,
    c.account_handle AS handle,
    COALESCE(NULLIF(c.account_display_name, ''), c.account_handle) AS display_name,
    c.account_url AS profile_url,
    c.avatar_url,
    ''::text AS country,
    'company'::varchar(20) AS account_type,
    CASE
      WHEN COALESCE(m.followers, c.self_reported_followers, 0) >= 100000 THEN '头部'
      WHEN COALESCE(m.followers, c.self_reported_followers, 0) >= 10000 THEN '腰部'
      ELSE '尾部'
    END::varchar(20) AS tier,
    COALESCE(m.followers, c.self_reported_followers, 0)::bigint AS followers,
    COALESCE(m.posts_count, c.self_reported_posts, 0)::bigint AS posts_count,
    COALESCE(m.total_views, 0)::bigint AS total_views,
    COALESCE(m.total_likes, 0)::bigint AS total_likes,
    COALESCE(m.total_comments, 0)::bigint AS total_comments,
    COALESCE(m.total_shares, 0)::bigint AS total_shares,
    m.engagement_rate,
    m.captured_at AS last_synced_at,
    NULL::numeric(9,6) AS latitude,
    NULL::numeric(9,6) AS longitude,
    true AS is_official,
    false AS has_video_evidence,
    0::integer AS video_evidence_count,
    NULL::date AS first_video_at,
    NULL::date AS last_video_at
  FROM vkpi_employee_channels c
  LEFT JOIN latest_official_metric m ON m.channel_id = c.id
  WHERE c.deleted_at IS NULL
    AND c.status = 'active'
    AND (
      POSITION('official_account' IN COALESCE(c.metadata_json::text, '')) > 0
      OR POSITION('official_list_20260516' IN COALESCE(c.metadata_json::text, '')) > 0
      OR POSITION('official_account_list_2026_05_16' IN COALESCE(c.metadata_json::text, '')) > 0
    )
),
pool_classified AS (
  SELECT
    p.*,
    CASE
      WHEN lower(COALESCE(p.handle, '')) LIKE '%viltrox%'
        OR lower(COALESCE(p.display_name, '')) LIKE '%viltrox%'
        OR COALESCE(p.display_name, '') LIKE '%唯卓仕%' THEN 'company'
      WHEN lower(COALESCE(p.platform, '')) = 'media'
        OR COALESCE(p.display_name, '') ILIKE '%media%'
        OR lower(COALESCE(p.handle, '')) LIKE '%digitalcameraworld%'
        OR lower(COALESCE(p.handle, '')) LIKE '%opticallimits%'
        OR lower(COALESCE(p.handle, '')) LIKE '%phillipreeve%'
        OR lower(COALESCE(p.handle, '')) LIKE '%sonyalpha%'
        OR lower(COALESCE(p.handle, '')) LIKE '%petapixel%'
        OR lower(COALESCE(p.handle, '')) LIKE '%dpreview%'
        OR lower(COALESCE(p.handle, '')) LIKE '%fstoppers%'
        OR lower(COALESCE(p.handle, '')) LIKE '%photographyblog%'
        OR lower(COALESCE(p.handle, '')) LIKE '%photographylife%'
        OR lower(COALESCE(p.handle, '')) LIKE '%thephoblographer%'
        OR lower(COALESCE(p.handle, '')) LIKE '%35mmc%'
        OR lower(COALESCE(p.handle, '')) LIKE '%admiringlight%'
        OR lower(COALESCE(p.handle, '')) LIKE '%amateurphotographer%'
        OR lower(COALESCE(p.handle, '')) LIKE '%cameralabs%'
        OR lower(COALESCE(p.handle, '')) LIKE '%imaging-resource%'
        OR lower(COALESCE(p.handle, '')) LIKE '%kenrockwell%'
        OR lower(COALESCE(p.handle, '')) LIKE '%lensrentals%'
        OR lower(COALESCE(p.handle, '')) LIKE '%lensreviewer%'
        OR lower(COALESCE(p.handle, '')) LIKE '%lensvid%'
        OR lower(COALESCE(p.handle, '')) LIKE '%lenstip%'
        OR lower(COALESCE(p.handle, '')) LIKE '%mirrorlessons%'
        OR lower(COALESCE(p.handle, '')) LIKE '%photographyreview%' THEN 'media'
      ELSE COALESCE(NULLIF(p.dashboard_account_type, ''), 'kol')
    END::varchar(20) AS computed_account_type,
    COALESCE(
      NULLIF(p.dashboard_tier, ''),
      CASE
        WHEN COALESCE(p.followers, 0) >= 100000 THEN '头部'
        WHEN COALESCE(p.followers, 0) >= 10000 THEN '腰部'
        ELSE '尾部'
      END
    )::varchar(20) AS computed_tier
  FROM vkpi_kol_pool p
),
pool_accounts AS (
  SELECT
    'pool:' || p.id::text AS dashboard_id,
    p.id AS source_id,
    'vkpi_kol_pool'::text AS source_table,
    p.platform,
    p.handle,
    COALESCE(NULLIF(p.display_name, ''), p.handle) AS display_name,
    p.profile_url,
    p.avatar_url,
    p.country,
    p.computed_account_type AS account_type,
    p.computed_tier AS tier,
    COALESCE(p.followers, 0)::bigint AS followers,
    COALESCE(p.posts_count, 0)::bigint AS posts_count,
    COALESCE(p.avg_views, 0)::bigint AS total_views,
    COALESCE(p.avg_likes, 0)::bigint AS total_likes,
    COALESCE(p.avg_comments, 0)::bigint AS total_comments,
    COALESCE(p.avg_shares, 0)::bigint AS total_shares,
    p.engagement_rate,
    p.last_seen_at AS last_synced_at,
    p.dashboard_latitude AS latitude,
    p.dashboard_longitude AS longitude,
    false AS is_official,
    COALESCE(p.has_video_evidence, false) AS has_video_evidence,
    COALESCE(p.video_evidence_count, 0)::integer AS video_evidence_count,
    p.first_video_at,
    p.last_video_at
  FROM pool_classified p
  WHERE p.computed_account_type <> 'company'
)
SELECT * FROM official_accounts
UNION ALL
SELECT * FROM pool_accounts;

COMMENT ON VIEW v_dashboard_account_pool IS
  'Unified Dashboard KOL Picker account pool: official company accounts + vkpi_kol_pool KOL/media candidates, with evidence active fields for KOL/media.';
