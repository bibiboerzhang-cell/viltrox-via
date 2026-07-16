import { useState } from "react";

import { proxiedImageUrl } from "../../shared/mediaProxy";
import { asRecord, cleanText, type Row } from "./SmartKolInputPanel.helpers";

export function secondsLabel(value: unknown): string {
  const total = Math.round(Number(value));
  if (!Number.isFinite(total) || total <= 0) return "";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function dateLabel(value: unknown): string {
  const raw = cleanText(value);
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(parsed);
}

export function safeHttpUrl(value: unknown): string {
  const raw = cleanText(value);
  if (!raw || raw.startsWith("//")) return "";
  try {
    const parsed = new URL(raw);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return "";
    return raw;
  } catch {
    return "";
  }
}

export function firstSafeHttpUrl(...values: unknown[]): string {
  for (const value of values) {
    const safe = safeHttpUrl(value);
    if (safe) return safe;
  }
  return "";
}

export function safeCachedVideoUrl(value: unknown): string {
  const raw = cleanText(value);
  if (/^\/api\/vkpi-media\/video-cache\/[a-f0-9]{64}$/i.test(raw)) return raw;
  return safeHttpUrl(raw);
}

export function firstSafeCachedVideoUrl(...values: unknown[]): string {
  for (const value of values) {
    const safe = safeCachedVideoUrl(value);
    if (safe) return safe;
  }
  return "";
}

const PUBLIC_PROFILE_FIELDS = [
  "platform",
  "handle",
  "display_name",
  "avatar_url",
  "followers",
  "subscriber_count",
  "posts_count",
  "video_count",
  "bio",
  "description",
  "profile_url",
  "channel_url",
  "channel_id",
] as const;

export function publicProfileFields(value: unknown): Row {
  const source = asRecord(value);
  const result: Row = {};
  for (const key of PUBLIC_PROFILE_FIELDS) {
    const candidate = source[key];
    if (["followers", "subscriber_count", "posts_count", "video_count"].includes(key)) {
      const count = Number(candidate);
      if (Number.isFinite(count) && count > 0) result[key] = count;
      continue;
    }
    if (["avatar_url", "profile_url", "channel_url"].includes(key)) {
      const safe = safeHttpUrl(candidate);
      if (safe) result[key] = safe;
      continue;
    }
    const text = cleanText(candidate);
    if (text) result[key] = text;
  }
  return result;
}

export function mergePublicProfiles(...sources: unknown[]): Row {
  return Object.assign({}, ...sources.map(publicProfileFields));
}

const PUBLIC_VIDEO_METADATA_FIELDS = [
  "channel_name",
  "title",
  "content_url",
  "thumbnail_url",
  "view_count",
  "play_count",
  "views",
  "like_count",
  "likes",
  "comment_count",
  "comments",
  "duration_seconds",
  "publish_date",
  "posted_at",
  "media_kind",
] as const;

const PUBLIC_RAW_FIELD_NAMES = Array.from(new Set<string>([
  ...PUBLIC_PROFILE_FIELDS,
  ...PUBLIC_VIDEO_METADATA_FIELDS,
]));

export function publicFieldRows(...sources: Row[]) {
  const merged = Object.assign({}, ...sources);
  const urlFields = new Set(["avatar_url", "profile_url", "channel_url", "content_url", "thumbnail_url"]);
  return PUBLIC_RAW_FIELD_NAMES
    .map((key) => {
      const value = merged[key];
      if (!["string", "number", "boolean"].includes(typeof value)) return [key, ""] as const;
      return [key, urlFields.has(key) ? safeHttpUrl(value) : cleanText(value)] as const;
    })
    .filter(([, value]) => Boolean(value));
}

export function VideoPoster({ source, title }: { source: string; title: string }) {
  const proxied = proxiedImageUrl(source);
  const posterKey = proxied || source;
  const [failedSource, setFailedSource] = useState("");
  const failed = Boolean(posterKey) && failedSource === posterKey;

  if (!source || failed) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-slate-950 text-[11px] text-slate-500">
        封面暂不可用
      </div>
    );
  }
  return (
    <img
      src={proxied || source}
      alt={title ? `${title} · 视频封面` : "视频封面"}
      className="absolute inset-0 h-full w-full bg-black object-contain"
      referrerPolicy="no-referrer"
      // IG/TikTok signed CDN 必须只走同源代理；代理失败后直连 raw CDN 会重新制造浏览器 403。
      // 对无需代理的同源/安全源，src 本身已经是 source，失败同样诚实落占位。
      onError={() => setFailedSource(posterKey)}
    />
  );
}

// A·上框:视频 URL 的创作者账号信息卡。主信息由 creator_identity/profile_data/池档案合并，
// video_metadata 只补平台/频道标识，不再把视频文案误当账号简介；原始字段退到折叠区。
