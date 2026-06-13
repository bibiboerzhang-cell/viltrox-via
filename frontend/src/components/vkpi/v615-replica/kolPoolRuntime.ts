import type { VkpiKolPoolItem } from "../../../domains/kol";
import { normalizeCountryCode } from "./data/countryInfo";

const GEO_A = new Set(["US", "CA", "UK", "DE", "JP", "AU", "SE", "NL", "KR", "SG"]);
const GEO_B = new Set(["FR", "IT", "ES", "AT", "CH", "BE", "TW", "HK", "MY", "AE", "IE", "RU", "ZA", "NZ"]);

function normalizeCountry(value?: string) {
  return normalizeCountryCode(value);
}

function normalizePercent(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.abs(value) <= 1 ? value * 100 : value;
}

function numberOrNull(value?: number) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function valueFrom(item: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = item[key];
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function parseObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== "string" || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function parseList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value !== "string") return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return value.split(",").map((item) => item.trim()).filter(Boolean);
  }
}

function firstValue(...values: unknown[]) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return null;
}

function nestedObject(parent: Record<string, unknown>, key: string): Record<string, unknown> {
  return parseObject(parent[key]);
}

function nestedList(parent: Record<string, unknown>, key: string): unknown[] {
  const value = parent[key];
  return Array.isArray(value) ? value : [];
}

function numberFromRecord(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const raw = record[key];
    const numeric = typeof raw === "number" ? raw : typeof raw === "string" ? Number(raw.replace(/,/g, "")) : NaN;
    if (Number.isFinite(numeric)) return numeric;
  }
  return null;
}

function toBrandHistory(items: string[]) {
  return items.slice(0, 6).map((brand) => ({
    brand,
    year: "",
    deal: "来源字段记录",
  }));
}

function representativeVideosFrom(raw: Record<string, unknown>) {
  const profile = nestedObject(raw, "profile");
  const profileVideos = nestedList(profile, "videos");
  const videos = [
    ...nestedList(raw, "videos"),
    ...profileVideos,
    ...nestedList(nestedObject(raw, "raw"), "videos"),
  ].filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === "object" && !Array.isArray(entry)));
  return videos.slice(0, 3).map((video, index) => {
    const stats = nestedObject(video, "statistics");
    const snippet = nestedObject(video, "snippet");
    return {
      title: String(firstValue(video.title, snippet.title, video.text, video.caption, `代表作 ${index + 1}`)),
      views: numberFromRecord(video, ["views", "view_count", "playCount", "viewCount"]) ?? numberFromRecord(stats, ["viewCount"]) ?? "—",
      duration: String(firstValue(video.duration, video.duration_text, video.length, "—")),
      url: String(firstValue(video.url, video.post_url, video.permalink, video.webpage_url, "")),
    };
  });
}

function representativeVideosFromEvidence(items: unknown) {
  if (!Array.isArray(items)) return [];
  return items
    .filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === "object" && !Array.isArray(entry)))
    .slice(0, 3)
    .map((video, index) => {
      const durationSeconds = Number(video.duration_seconds);
      const duration = !Number.isFinite(durationSeconds)
        ? "—"
        : `${Math.floor(durationSeconds / 60)}:${Math.round(durationSeconds % 60).toString().padStart(2, "0")}`;
      return {
        evidence_id: numberFromRecord(video, ["evidence_id", "id"]),
        title: String(firstValue(video.title, video.video_title, video.content_url, `代表作 ${index + 1}`)),
        views: numberFromRecord(video, ["view_count", "views"]) ?? "—",
        duration,
        url: String(firstValue(video.content_url, video.url, "")),
        thumbnail_url: String(firstValue(video.thumbnail_url, "")),
        best_thumbnail: String(firstValue(video.best_thumbnail, video.thumbnail_url, "")),
        cached_video_url: String(firstValue(video.cached_video_url, "")),
        youtube_video_id: String(firstValue(video.youtube_video_id, "")),
        youtube_thumbnail_url: String(firstValue(video.youtube_thumbnail_url, "")),
        watch_url: String(firstValue(video.watch_url, video.content_url, video.url, "")),
        platform: String(firstValue(video.platform, "")),
        published_at: String(firstValue(video.publish_date, video.posted_at, "")),
      };
    });
}

function avatarColor(seed: string) {
  const colors = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ec4899", "#06b6d4"];
  let hash = 0;
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) % colors.length;
  return colors[Math.abs(hash)];
}

export function toV615KolPoolRows(items: VkpiKolPoolItem[]) {
  return items.map((item, index) => {
    const raw = item as unknown as Record<string, unknown>;
    const rawPlatformData = parseObject(item.raw_platform_data);
    const rawIdentity = nestedObject(rawPlatformData, "identity");
    const rawEvidence = nestedObject(rawPlatformData, "evidence_summary");
    const country = normalizeCountry(String(firstValue(item.country, rawPlatformData.country, nestedObject(rawPlatformData, "raw").country) || ""));
    const followers = numberOrNull(item.followers);
    const realEr = normalizePercent(numberOrNull(raw.real_engagement_rate as number) ?? numberOrNull(item.engagement_rate));
    const realFollowers = normalizePercent(numberOrNull(raw.real_followers_pct as number) ?? numberOrNull(raw.real_followers_percent as number));
    const fit = numberOrNull(item.viltrox_fit_score);
    const stale = ["stale", "needs_refresh", "missing", "partial"].includes(String(item.sync_status || "").toLowerCase());
    const missingCore = !followers || !country || realEr == null || fit == null;
    const candidateKind = item.linked_main_kol_id
      ? missingCore || stale ? "existing_low_confidence" : "existing_fresh"
      : fit != null && fit >= 80 ? "new_promoted" : "new_discovered";
    const avgViews = numberOrNull(item.avg_views);
    const estimatedReach = country && avgViews != null ? { [country]: Math.round(avgViews) } : undefined;
    const trendScore = normalizePercent(numberOrNull(raw.trend_score as number) ?? numberOrNull(raw.trend_resonance as number));
    const recommendedProductLines = [
      ...parseList(item.recommended_product_lines_json),
      ...parseList(rawPlatformData.recommended_product_lines),
    ];
    const potentialConcerns = [
      ...parseList(item.potential_concerns_json),
      ...parseList(rawPlatformData.potential_concerns),
    ];
    const brandCollaborationNames = [
      ...parseList(item.brand_collaborations_json),
      ...parseList(rawPlatformData.brand_collaborations),
    ];
    const competitorBrands = brandCollaborationNames
      .filter((brand) => !/viltrox/i.test(brand))
      .slice(0, 2);
    const hasViltrox = brandCollaborationNames.some((brand) => /viltrox/i.test(brand));
    const devicePrimary = firstValue(
      valueFrom(raw, ["device_primary", "camera_body", "primary_camera", "gear_primary"]),
      rawPlatformData.device_primary,
      nestedObject(rawPlatformData, "devices").camera_body,
    );
    const evidenceVideos = representativeVideosFromEvidence(raw.video_evidence);
    const representativeVideos = evidenceVideos.length ? evidenceVideos : representativeVideosFrom(rawPlatformData);
    const profileUrl = String(firstValue(item.profile_url, rawIdentity.profile_url, rawPlatformData.profile_url, "") || "");
    const displayName = String(firstValue(item.display_name, rawIdentity.display_name, item.handle, "待补全"));
    const evidenceCount = numberFromRecord(rawEvidence, ["evidence_count"]);

    return {
      id: item.id || index + 1,
      pool_uid: item.pool_uid,
      platform: item.platform || "kol-pool",
      handle: item.handle || displayName || item.pool_uid || `kol-${index + 1}`,
      display_name: displayName,
      profile_url: profileUrl,
      email: item.email,
      bio: item.bio || item.viltrox_fit_reason || (evidenceCount ? `本地证据 ${evidenceCount} 条，画像待补全` : "待接入真实画像"),
      country,
      // C-fix:账户信息此前被本固定键白名单滤掉,抽屉拿不到 → 透传(后端 detail item 走 SELECT *,
      // pool_common.py 列已含 language/following/posts_count/avg_*;first/last_video_at 落 raw_platform_data)。
      language: firstValue(valueFrom(raw, ["language"]), rawPlatformData.language) || null,
      following: numberOrNull(item.following),
      posts_count: numberOrNull(item.posts_count),
      avg_views: numberOrNull(item.avg_views),
      avg_likes: numberOrNull(item.avg_likes),
      avg_comments: numberOrNull(item.avg_comments),
      first_video_at: firstValue(valueFrom(raw, ["first_video_at"]), rawPlatformData.first_video_at) || null,
      last_video_at: firstValue(valueFrom(raw, ["last_video_at"]), rawPlatformData.last_video_at, item.last_seen_at) || null,
      followers,
      real_followers_pct: realFollowers,
      real_er_pct: realEr,
      engagement_rate: realEr,
      er_calibration: realEr == null ? null : 0,
      candidate_kind: candidateKind,
      linked_main_kol_id: item.linked_main_kol_id,
      audience_type: valueFrom(raw, ["audience_type"]) || null,
      geo_tier: GEO_A.has(country) ? "A" : GEO_B.has(country) ? "B" : country ? "C" : "X",
      geo_distribution: country ? [{ country, share: 1 }] : [],
      trend_resonance: trendScore == null ? null : trendScore / 100,
      trend_hits: raw.trend_hits ? parseList(raw.trend_hits) : [],
      v6_fit: fit,
      viltrox_fit_reason: item.viltrox_fit_reason,
      // d4(D2 点亮):后端 get_item/detail_bundle 已输出只读投影 v6_breakdown(pool.py:611,
      // rule_v0 重算的 read projection,2b44eebc),此前被本映射的固定键集合滤掉——透传即通电。
      // 注:投影的十个乘数槽是中性 1.0(legacy slot),真加法组件在 components 键;
      // 因子真值持久化归 E2(Codex)。列表 list 端点不带此键,值为 null,Drawer 显 "—" 不受影响。
      v6_breakdown: (item as unknown as Record<string, unknown>).v6_breakdown || null,
      // d3 量纲修复:fit 是 0-100,loyalty 全部消费点(Drawer fixedOrDash(x,2)、email.ts 阈值 0.85)
      // 按 0-1 设计;原先直塞 fit 导致 email 高忠诚分支恒 true、Drawer 显示 "45.00"。
      loyalty_score: fit == null ? null : Math.round(fit) / 100,
      // d3 geo_match 透传:email.ts 按 (geo_match||0)>0.9 分支,此前该键从未输出恒 undefined。
      // 启发式信号仅供邮件文案分支:A 档市场 0.95 / B 档 0.6 / 其他有国家 0.3 / 未知 null。
      geo_match: GEO_A.has(country) ? 0.95 : GEO_B.has(country) ? 0.6 : country ? 0.3 : null,
      upgrade_factor: fit,
      estimated_country_reach: estimatedReach,
      representative_videos: representativeVideos,
      recommended_product_lines: Array.from(new Set(recommendedProductLines)).slice(0, 5),
      potential_concerns: Array.from(new Set(potentialConcerns)).slice(0, 5),
      brand_collaborations: toBrandHistory(Array.from(new Set(brandCollaborationNames))),
      competitor_collabs: competitorBrands,
      // d8:后端列表 payload 新供两计数(KolPoolAllModal『已分析』chip 的既有判定键)。
      video_evidence_count: (item as unknown as Record<string, unknown>).video_evidence_count ?? null,
      llm_deep_analysis_count: (item as unknown as Record<string, unknown>).llm_deep_analysis_count ?? null,
      // d14:as-of 戳数据源(列表头部"数据截至"——脉冲恢复前,诚实陈旧比假新鲜值钱)。
      last_seen_at: (item as unknown as Record<string, unknown>).last_seen_at ?? null,
      refresh_state: item.sync_status || "fresh",
      industry_label: item.content_style || item.primary_topic || "真实 KOL Pool",
      weekly_views_delta: null,
      avatar_color: avatarColor(item.handle || item.display_name || String(index)),
      devices: {
        camera_body: devicePrimary || "待接入",
        lenses: [],
        has_viltrox: hasViltrox,
        competitor_brands: competitorBrands,
        upgrade_window: valueFrom(raw, ["upgrade_window"]) || "待评估",
      },
    };
  });
}
