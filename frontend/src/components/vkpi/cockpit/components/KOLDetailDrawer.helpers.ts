// 纯重构:从 KOLDetailDrawer.tsx 抽出的共享纯 helper(行为逐字搬运,零改)。
// 主文件与各 KOLDrawer* 子组件统一从此处 import,对外行为不变。

import { proxiedImageUrl } from "../../shared/mediaProxy";
import { type AnalysisBundle } from "./KOLVideoAnalysisPanel";

export function asArray(value: any) {
  return Array.isArray(value) ? value : [];
}

export function numberOr(value: any, fallback: any = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" && value.trim() === "") return fallback;
  const numeric = typeof value === "number" ? value : Number(String(value ?? "").replace(/[% ,]/g, ""));
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function fixedOrDash(value: any, digits = 2) {
  const numeric = numberOr(value);
  return numeric == null ? "—" : numeric.toFixed(digits);
}

export function pctOrZero(value: any) {
  return numberOr(value, 0) * 100;
}

export function scoreValue(value: any, fallback = 0) {
  const numeric = numberOr(value);
  if (numeric == null) return fallback;
  return Math.max(0, Math.min(100, numeric));
}

export function scoreText(value: any) {
  const numeric = numberOr(value);
  if (numeric == null) return "—";
  return String(Math.round(Math.max(0, Math.min(100, numeric))));
}

export function recordOr(value: any): any {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function compactText(value: any, max = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

export function concernLabel(value: any) {
  const text = String(value || "").trim();
  const labels = {
    contact_missing: "联系方式缺失",
    missing_kol_profile: "主表画像缺失",
    no_cooperation_history: "暂无合作历史",
    risk_watchlist: "风险观察名单",
  };
  return (labels as any)[text] || text.replace(/_/g, " ");
}

// ─── 以下为从 KOLDetailDrawer.tsx 行为不变搬入的纯 helper(无 React/JSX)。 ───

export function detailAvatarUrl(item: any) {
  if (!item || typeof item !== "object") return "";
  return proxiedImageUrl(
    item.avatar_url ||
    item.avatarUrl ||
    item.avatar_image_url ||
    item.profile_image_url ||
    item.profileImageUrl ||
    item.source_fields?.avatar_url ||
    item.source_fields?.profile_image_url ||
    ""
  );
}

export function flexibleTextList(value: any, maxItems = 3) {
  const out: any[] = [];
  const push = (item: any) => {
    const text = compactText(item, 220);
    if (text) out.push(text);
  };
  const visit = (item: any) => {
    if (item === null || item === undefined || out.length >= maxItems) return;
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      push(item);
      return;
    }
    if (Array.isArray(item)) {
      item.forEach(visit);
      return;
    }
    if (typeof item === "object") {
      const record = recordOr(item);
      const parts = ["recommendation", "reason", "summary", "evidence", "rationale"]
        .map((key) => record[key])
        .filter((part: any) => typeof part === "string" && part.trim());
      if (parts.length > 1) {
        push(parts.join(" · "));
        return;
      }
      const direct = record.text || record.summary || record.reason || record.rationale || record.description || record.notes || record.evidence || record.recommendation || record.final_verdict || record.key_hook || record.flag || record.label || record.title;
      if (direct) {
        const prefix = record.severity ? "[" + record.severity + "] " : record.status ? "[" + record.status + "] " : "";
        push(prefix + String(direct));
        return;
      }
      if (parts.length) push(parts.join(" · "));
    }
  };
  visit(value);
  return out.slice(0, maxItems);
}

export function maxScore(record: any) {
  const values = Object.values(recordOr(record)).map((value: any) => numberOr(value)).filter((value: any) => value != null);
  return values.length ? Math.max(...values) : 0;
}

export function dimensions11RadarDims(payload: any) {
  if (!payload || typeof payload !== "object" || payload.status === "missing" || payload.persisted === false) {
    return [];
  }
  const block1 = recordOr(payload.block1_content);
  const block2 = recordOr(payload.block2_performance);
  const block3 = recordOr(payload.block3_business);
  const block4 = recordOr(payload.block4_specialty);
  if (![block1, block2, block3, block4].some((block) => Object.keys(block).length)) return [];
  const specialtyScore = maxScore(block1.content_specialty);
  const productFitScore = maxScore(block4.product_fit);
  const clusters = asArray(block4.industry_cluster).filter(Boolean);
  const industryScore = clusters.length ? 82 : 0;
  const competitorRisk = scoreValue(block3.competitor_risk_score, 0);
  return [
    { label: "Fit",      value: productFitScore, source: "block4.product_fit" },
    { label: "Reach",    value: block2.followers_tier_score, source: "block2.followers_tier_score" },
    { label: "ER",       value: block2.engagement_quality_score, source: "block2.engagement_quality_score" },
    { label: "Quality",  value: block1.content_diversity_score, source: "block1.content_diversity_score" },
    { label: "Style",    value: specialtyScore, source: "block1.content_specialty" },
    { label: "Audience", value: industryScore, source: "block4.industry_cluster" },
    { label: "Growth",   value: block2.growth_velocity_score, source: "block2.growth_velocity_score" },
    { label: "Brand",    value: block3.cooperation_history_score, source: "block3.cooperation_history_score" },
    { label: "Risk",     value: 100 - competitorRisk, source: "100 - block3.competitor_risk_score" },
    { label: "Comm",     value: block3.contact_reachability_score, source: "block3.contact_reachability_score" },
    { label: "Activity", value: block1.posting_frequency_score, source: "block1.posting_frequency_score" },
  ].map((dimension) => ({
    ...dimension,
    value: scoreValue(dimension.value, 0),
  }));
}

export function videoAnalysisSources(item: any, representativeVideos: any) {
  const seen = new Set();
  return [...asArray(item.video_evidence), ...representativeVideos].filter((video) => {
    if (!video || typeof video !== "object") return false;
    const key = String(video.evidence_id || video.id || video.content_url || video.url || video.title || "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function cacheEntryOrNull(value: any) {
  const record = recordOr(value);
  return Object.keys(record).length ? record : null;
}

export function detailBundleAnalysisItems(detailBundle: any) {
  const videoAnalysis = recordOr(recordOr(detailBundle).video_analysis);
  return asArray(videoAnalysis.items).map((item) => {
    const record = recordOr(item);
    return {
      video: recordOr(record.video),
      finalEntry: cacheEntryOrNull(record.final_entry),
      qaEntry: cacheEntryOrNull(record.qa_entry),
    };
  }).filter((item) => Object.keys(item.video).length);
}

export function detailBundleAnalysisSummary(detailBundle: any) {
  const summary = recordOr(recordOr(recordOr(detailBundle).video_analysis).summary);
  return Object.keys(summary).length ? summary : null;
}

export function evidenceIdOf(video: any) {
  if (!video || typeof video !== "object") return null;
  const value = video.evidence_id ?? video.id;
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function videoString(video: any, keys: any, fallback = "") {
  const source = recordOr(video);
  for (const key of keys) {
    const value = source[key];
    if (value !== null && value !== undefined && String(value).trim()) return String(value).trim();
  }
  return fallback;
}

export function hostFromUrl(url: any) {
  try {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const normalized = raw.includes("://") ? raw : `https://${raw}`;
    return new URL(normalized).hostname.toLowerCase();
  } catch {
    return "";
  }
}

export function parseYoutubeVideoId(url: any) {
  try {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const normalized = raw.includes("://") ? raw : `https://${raw}`;
    const parsed = new URL(normalized);
    const host = parsed.hostname.toLowerCase();
    if (host.includes("youtu.be")) {
      return parsed.pathname.split("/").filter(Boolean)[0] || "";
    }
    if (!host.includes("youtube.com")) return "";
    const watchId = parsed.searchParams.get("v");
    if (watchId) return watchId;
    const parts = parsed.pathname.split("/").filter(Boolean);
    const marker = parts.findIndex((part) => ["embed", "shorts", "live"].includes(part));
    return marker >= 0 ? parts[marker + 1] || "" : "";
  } catch {
    return "";
  }
}

export function normalizedVideoPlatform(video: any) {
  const explicit = videoString(video, ["platform"]).toLowerCase();
  if (explicit) return explicit;
  const url = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(url);
  if (host.includes("youtube.com") || host.includes("youtu.be")) return "youtube";
  if (host.includes("instagram.com")) return "instagram";
  if (host.includes("tiktok.com")) return "tiktok";
  return "media";
}

export function youtubeIdForVideo(video: any) {
  const platform = normalizedVideoPlatform(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(watchUrl);
  if (platform !== "youtube" && !host.includes("youtube.com") && !host.includes("youtu.be")) return "";
  return videoString(video, ["youtube_video_id"]) || parseYoutubeVideoId(watchUrl);
}

export function youtubeEmbedUrl(videoId: any) {
  const id = String(videoId || "").trim();
  if (!id) return "";
  const origin = typeof window !== "undefined" && window.location?.origin
    ? `&origin=${encodeURIComponent(window.location.origin)}`
    : "";
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1&modestbranding=1${origin}`;
}

export function videoUrlKey(video: any) {
  return videoString(video, ["watch_url", "url", "content_url"]).toLowerCase();
}

export function matchAnalysisBundle(video: any, bundles: any): AnalysisBundle | null {
  if (!Array.isArray(bundles)) return null;
  const targetId = evidenceIdOf(video);
  if (targetId) {
    const byId = bundles.find((bundle: any) => evidenceIdOf(recordOr(bundle).video) === targetId);
    if (byId) return byId;
  }
  const targetUrl = videoUrlKey(video);
  if (targetUrl) {
    const byUrl = bundles.find((bundle: any) => videoUrlKey(recordOr(bundle).video) === targetUrl);
    if (byUrl) return byUrl;
  }
  return null;
}
