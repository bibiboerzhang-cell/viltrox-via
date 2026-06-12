// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Activity, AlertTriangle, BadgeCheck, Camera, Check, ExternalLink, Flame, Globe2, Heart, Layers, Link2, MapPin, MoreHorizontal, RefreshCw, Send, Shield, ShoppingBag, Sparkles, Star, Target, UserPlus, Video, X, Zap } from "lucide-react";
import { AudienceTypeChip } from "./AudienceTypeChip";
import { CandidateKindChip } from "./CandidateKindChip";
import { GeoTierChip } from "./GeoTierChip";
import { KPAvatar } from "./KPAvatar";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";
import { PlatformPill } from "./PlatformPill";
import { enqueueVideoAnalysis, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis } from "../../../../services/vkpi/kolPool-api";
import { candidateKindGroup } from "../lib/candidateKind";
import { formatNumber, formatPercent } from "../lib/format";
import { BRAND_TIER } from "../data/brandTier";
import { COUNTRY_INFO, getCountryInfo } from "../data/countryInfo";
import { TREND_PULSE_THIS_WEEK } from "../data/trendPulse";
import { proxiedImageUrl, proxiedVideoUrl } from "../../shared/mediaProxy";

const e = React.createElement;

// d1:真复制按钮(原按钮有 title 无 onClick,点击无声失败——B2 全页唯一假按钮)
function CopyEmailButton({ email }) {
  const [copied, setCopied] = React.useState(false);
  return e("button", {
    className: "ml-auto p-1 rounded hover:bg-white/[0.04] " + (copied ? "text-emerald-300" : "text-slate-400 hover:text-white"),
    title: copied ? "已复制" : "复制邮箱",
    onClick: () => {
      void navigator.clipboard?.writeText(String(email || "")).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      });
    },
  }, e(copied ? Check : Link2, { size: 10 }));
}

function detailAvatarUrl(item) {
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

function KOLDetailAvatar({ item, size = 44 }) {
  const [failed, setFailed] = React.useState(false);
  const avatar = failed ? "" : detailAvatarUrl(item);
  if (!avatar) {
    return e(KPAvatar, { name: item.display_name || item.handle, color: item.avatar_color, size });
  }
  return e("span", {
    className: "shrink-0 overflow-hidden rounded-full border border-white/[0.08] bg-white/[0.04]",
    style: { width: size, height: size }
  },
    e("img", {
      src: avatar,
      alt: "",
      className: "h-full w-full object-cover",
      referrerPolicy: "no-referrer",
      onError: () => setFailed(true),
    })
  );
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberOr(value, fallback = null) {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" && value.trim() === "") return fallback;
  const numeric = typeof value === "number" ? value : Number(String(value ?? "").replace(/[% ,]/g, ""));
  return Number.isFinite(numeric) ? numeric : fallback;
}

function fixedOrDash(value, digits = 2) {
  const numeric = numberOr(value);
  return numeric == null ? "—" : numeric.toFixed(digits);
}

function pctOrZero(value) {
  return numberOr(value, 0) * 100;
}

function scoreValue(value, fallback = 0) {
  const numeric = numberOr(value);
  if (numeric == null) return fallback;
  return Math.max(0, Math.min(100, numeric));
}

function scoreText(value) {
  const numeric = numberOr(value);
  if (numeric == null) return "—";
  return String(Math.round(Math.max(0, Math.min(100, numeric))));
}

function recordOr(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function compactText(value, max = 180) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

function flexibleTextList(value, maxItems = 3) {
  const out = [];
  const push = (item) => {
    const text = compactText(item, 220);
    if (text) out.push(text);
  };
  const visit = (item) => {
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
        .filter((part) => typeof part === "string" && part.trim());
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

function maxScore(record) {
  const values = Object.values(recordOr(record)).map((value) => numberOr(value)).filter((value) => value != null);
  return values.length ? Math.max(...values) : 0;
}

function dimensions11RadarDims(payload) {
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

function concernLabel(value) {
  const text = String(value || "").trim();
  const labels = {
    contact_missing: "联系方式缺失",
    missing_kol_profile: "主表画像缺失",
    no_cooperation_history: "暂无合作历史",
    risk_watchlist: "风险观察名单",
  };
  return labels[text] || text.replace(/_/g, " ");
}

function videoAnalysisSources(item, representativeVideos) {
  const seen = new Set();
  return [...asArray(item.video_evidence), ...representativeVideos].filter((video) => {
    if (!video || typeof video !== "object") return false;
    const key = String(video.evidence_id || video.id || video.content_url || video.url || video.title || "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function cacheEntryOrNull(value) {
  const record = recordOr(value);
  return Object.keys(record).length ? record : null;
}

function detailBundleAnalysisItems(detailBundle) {
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

function evidenceIdOf(video) {
  if (!video || typeof video !== "object") return null;
  const value = video.evidence_id ?? video.id;
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
}

function videoString(video, keys, fallback = "") {
  const source = recordOr(video);
  for (const key of keys) {
    const value = source[key];
    if (value !== null && value !== undefined && String(value).trim()) return String(value).trim();
  }
  return fallback;
}

function hostFromUrl(url) {
  try {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const normalized = raw.includes("://") ? raw : `https://${raw}`;
    return new URL(normalized).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function parseYoutubeVideoId(url) {
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

function normalizedVideoPlatform(video) {
  const explicit = videoString(video, ["platform"]).toLowerCase();
  if (explicit) return explicit;
  const url = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(url);
  if (host.includes("youtube.com") || host.includes("youtu.be")) return "youtube";
  if (host.includes("instagram.com")) return "instagram";
  if (host.includes("tiktok.com")) return "tiktok";
  return "media";
}

function youtubeIdForVideo(video) {
  const platform = normalizedVideoPlatform(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(watchUrl);
  if (platform !== "youtube" && !host.includes("youtube.com") && !host.includes("youtu.be")) return "";
  return videoString(video, ["youtube_video_id"]) || parseYoutubeVideoId(watchUrl);
}

function youtubeEmbedUrl(videoId) {
  const id = String(videoId || "").trim();
  if (!id) return "";
  const origin = typeof window !== "undefined" && window.location?.origin
    ? `&origin=${encodeURIComponent(window.location.origin)}`
    : "";
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1&modestbranding=1${origin}`;
}

function RepresentativeVideoCard({ video, index, onOpen }) {
  const [thumbnailFailed, setThumbnailFailed] = React.useState(false);
  const title = videoString(video, ["title", "video_title"], `代表作 ${index + 1}`);
  const views = videoString(video, ["views", "view_count"], "—");
  const duration = videoString(video, ["duration"], "—");
  const thumbnail = proxiedImageUrl(videoString(video, ["best_thumbnail", "thumbnail_url", "youtube_thumbnail_url"]));
  const cachedVideoUrl = videoString(video, ["cached_video_url"]);
  const youtubeVideoId = youtubeIdForVideo(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const platform = normalizedVideoPlatform(video);
  const canOpen = Boolean(cachedVideoUrl || youtubeVideoId || watchUrl || thumbnail);
  const showThumbnail = Boolean(thumbnail && !thumbnailFailed);

  const handleClick = () => {
    if (!canOpen) return;
    onOpen?.(video);
  };

  const media = showThumbnail
    ? e("img", {
        src: thumbnail,
        alt: title,
        className: "absolute inset-0 h-full w-full object-cover",
        loading: "lazy",
        referrerPolicy: "no-referrer",
        onError: () => setThumbnailFailed(true),
      })
    : e(Video, { size: 16, className: "text-slate-500" });

  return e("button", {
    type: "button",
    className: [
      "w-full rounded-md border border-white/[0.06] bg-white/[0.02] overflow-hidden text-left transition-colors",
      canOpen ? "hover:bg-white/[0.04] cursor-pointer focus:outline-none focus:ring-1 focus:ring-cyan-300/30" : "cursor-default",
    ].filter(Boolean).join(" "),
    onClick: handleClick,
    title: canOpen ? "点击播放或打开代表作" : undefined,
  },
    e("div", {
      className: "aspect-video bg-gradient-to-br from-slate-700 to-slate-900 flex items-center justify-center relative overflow-hidden",
    },
      media,
      e("span", {
        className: "absolute left-1 top-1 rounded px-1 py-0.5 text-[7.5px] font-medium uppercase tracking-wide text-white/85",
        style: { background: "rgba(0,0,0,0.62)" }
      }, platform === "instagram" ? "IG" : platform === "tiktok" ? "TT" : platform === "youtube" ? "YT" : "MEDIA"),
      platform !== "youtube" && e("span", {
        className: "absolute right-1 top-1 rounded px-1 py-0.5 text-[7.5px] font-medium text-white/80",
        style: { background: cachedVideoUrl ? "rgba(16,185,129,0.58)" : "rgba(15,23,42,0.72)" }
      }, cachedVideoUrl ? "R2" : "无缓存"),
      e("span", {
        className: "absolute bottom-1 right-1 px-1 rounded text-[8px] tabular-nums text-white",
        style: { background: "rgba(0,0,0,0.7)" }
      }, duration)
    ),
    e("div", { className: "p-1.5" },
      e("div", { className: "text-[9px] text-white truncate leading-tight" }, title),
      e("div", { className: "text-[8px] text-slate-500 tabular-nums" }, views + " 播放")
    )
  );
}

function RepresentativeVideoPlayerModal({ video, onClose }) {
  const title = videoString(video, ["title", "video_title"], "代表作");
  const thumbnail = proxiedImageUrl(videoString(video, ["best_thumbnail", "thumbnail_url", "youtube_thumbnail_url"]));
  const cachedVideoUrl = proxiedVideoUrl(videoString(video, ["cached_video_url"]));
  const youtubeVideoId = youtubeIdForVideo(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const platform = normalizedVideoPlatform(video);
  const embedSrc = youtubeEmbedUrl(youtubeVideoId);

  React.useEffect(() => {
    const handleKey = (event) => {
      if (event.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const stage = cachedVideoUrl
    ? e("video", {
          src: cachedVideoUrl,
          poster: thumbnail || undefined,
          className: "h-full w-full rounded-lg bg-black object-contain",
          controls: true,
          autoPlay: true,
          playsInline: true,
        })
    : youtubeVideoId
      ? e("div", { className: "relative h-full w-full rounded-lg bg-black" },
          e("iframe", {
            src: embedSrc,
            title,
            className: "h-full w-full rounded-lg bg-black",
            allow: "accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share",
            allowFullScreen: true,
            loading: "eager",
            referrerPolicy: "strict-origin-when-cross-origin",
          }),
          watchUrl && e("a", {
            href: watchUrl,
            target: "_blank",
            rel: "noreferrer",
            className: "absolute bottom-3 right-3 rounded-md border border-white/12 bg-black/70 px-2 py-1 text-[10px] font-medium text-white/80 backdrop-blur hover:bg-black/85 hover:text-white",
          }, "黑屏则打开原帖")
        )
      : e("div", {
          className: "flex h-full w-full flex-col items-center justify-center gap-4 rounded-lg border border-white/[0.08] bg-slate-950 p-8 text-center",
        },
          thumbnail
            ? e("img", {
                src: thumbnail,
                alt: title,
                className: "max-h-[55vh] max-w-full rounded-md object-contain",
                referrerPolicy: "no-referrer",
              })
            : e(Video, { size: 36, className: "text-slate-500" }),
          e("div", null,
            e("div", { className: "text-sm font-medium text-white" },
              platform === "instagram" || platform === "tiktok" ? "当前未命中 R2 视频缓存" : "当前没有可内嵌播放的视频缓存"
            ),
            e("div", { className: "mt-1 text-xs text-slate-500" },
              platform === "instagram" || platform === "tiktok" ? "不会使用 YouTube 播放器；可以打开原帖查看" : "可以打开原帖查看"
            )
          ),
          watchUrl && e("a", {
            href: watchUrl,
            target: "_blank",
            rel: "noreferrer",
            className: "rounded-md border border-cyan-300/25 bg-cyan-300/[0.08] px-3 py-1.5 text-xs font-medium text-cyan-100 hover:bg-cyan-300/[0.14]",
          }, "打开原帖")
        );

  return e("div", {
    className: "fixed inset-0 z-[10000] flex items-center justify-center bg-black/80 p-4 backdrop-blur-md",
    role: "dialog",
    "aria-modal": true,
    "aria-label": "代表作视频播放器",
    onClick: onClose,
  },
    e(motion.div, {
      initial: { opacity: 0, scale: 0.96, y: 16 },
      animate: { opacity: 1, scale: 1, y: 0 },
      exit: { opacity: 0, scale: 0.96, y: 16 },
      className: "w-full max-w-5xl overflow-hidden rounded-xl border border-white/10 bg-[#070b14] shadow-2xl",
      onClick: (event) => event.stopPropagation(),
    },
      e("header", { className: "flex items-start justify-between gap-3 border-b border-white/[0.08] px-4 py-3" },
        e("div", { className: "min-w-0" },
          e("div", { className: "text-[10px] uppercase tracking-wider text-cyan-200/70" }, platform + " · 代表作"),
          e("h3", { className: "mt-1 truncate text-sm font-semibold text-white", title }, title)
        ),
        e("button", {
          type: "button",
          onClick: onClose,
          className: "shrink-0 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-sm text-slate-300 hover:bg-white/[0.08] hover:text-white",
          "aria-label": "关闭播放器",
        }, "×")
      ),
      e("div", { className: "aspect-video w-full bg-black" }, stage),
      watchUrl && e("footer", { className: "flex items-center justify-between gap-3 border-t border-white/[0.08] px-4 py-2" },
        e("span", { className: "truncate text-[10px] text-slate-500" }, watchUrl),
        e("a", {
          href: watchUrl,
          target: "_blank",
          rel: "noreferrer",
          className: "shrink-0 rounded-md border border-white/[0.08] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-white",
        }, "打开原帖")
      )
    )
  );
}

function LlmDeepAnalysisPanel({ payload }) {
  if (!payload || payload.status !== "ready" || !payload.primary_result) return null;
  const primary = recordOr(payload.primary_result);
  const dimensions = recordOr(primary.llm_dimensions_11);
  const fitPayload = recordOr(dimensions.llm_v6_fit);
  const recommendations = recordOr(dimensions.recommendations);
  const risk = recordOr(dimensions.risk);
  const qa = recordOr(dimensions.qa);
  const llmScore = numberOr(primary.llm_v6_fit ?? fitPayload.score);
  const confidence = numberOr(primary.confidence ?? fitPayload.confidence);
  const qaPass = typeof primary.llm_qa_pass === "boolean" ? primary.llm_qa_pass : typeof qa.qa_pass === "boolean" ? qa.qa_pass : null;
  const hasQa = Boolean(primary.llm_has_qa || Object.keys(qa).length);
  const recommendationRows = [
    { label: "合作建议", value: recommendations.cooperation_recommendation },
    { label: "素材授权", value: recommendations.buyout_or_license_recommendation },
    { label: "推荐理由", value: recommendations.why },
  ].map((row) => ({ ...row, texts: flexibleTextList(row.value, row.label === "推荐理由" ? 1 : 2) })).filter((row) => row.texts.length);
  const riskRows = [
    ...flexibleTextList(risk.risk_flags, 3),
    ...flexibleTextList(qa.issues, 3),
  ].slice(0, 4);
  const keyHook = flexibleTextList(risk.key_hook, 1)[0] || flexibleTextList(risk.final_verdict, 1)[0];

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center justify-between gap-2 mb-2" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Sparkles, { size: 11, className: "text-fuchsia-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "LLM 深度判断")
      ),
      e("span", { className: "text-[8.5px] text-slate-600" }, "llm_v6_fit · independent from V6 Fit")
    ),
    e("div", { className: "rounded-md border border-fuchsia-400/15 bg-fuchsia-400/[0.035] p-2.5" },
      e("div", { className: "flex items-start justify-between gap-3" },
        e("div", null,
          e("div", { className: "text-[9px] uppercase tracking-wider text-fuchsia-200/80" }, "LLM判断 · 独立于V6 Fit"),
          e("div", { className: "mt-1 flex items-baseline gap-2" },
            e("span", { className: "text-2xl font-semibold tabular-nums text-white" }, llmScore == null ? "—" : scoreText(llmScore)),
            confidence != null && e("span", { className: "text-[9.5px] text-slate-500" }, "conf " + fixedOrDash(confidence, 2))
          )
        ),
        hasQa && e("span", {
          className: "inline-flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[9px] font-medium",
          style: qaPass === false
            ? { background: "rgba(244,63,94,0.12)", borderColor: "rgba(244,63,94,0.28)", color: "#fecdd3" }
            : { background: "rgba(16,185,129,0.12)", borderColor: "rgba(16,185,129,0.28)", color: "#bbf7d0" }
        },
          qaPass === false ? e(AlertTriangle, { size: 10 }) : e(Shield, { size: 10 }),
          qaPass === false ? "关键帧QA需复核" : "关键帧QA通过"
        )
      ),
      keyHook && e("div", { className: "mt-2 text-[10.5px] leading-relaxed text-slate-300" }, keyHook),
      recommendationRows.length > 0 && e("div", { className: "mt-2 space-y-1.5" },
        recommendationRows.map((row) => e("div", { key: row.label, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
          e("div", { className: "mb-0.5 text-[9px] text-fuchsia-200" }, row.label),
          row.texts.map((text, index) => e("div", { key: index, className: "text-[10px] leading-relaxed text-slate-300" }, text))
        ))
      ),
      riskRows.length > 0 && e("div", { className: "mt-2 rounded border border-amber-300/15 bg-amber-300/[0.04] px-2 py-1.5" },
        e("div", { className: "mb-1 flex items-center gap-1 text-[9px] text-amber-200" }, e(AlertTriangle, { size: 9 }), "风险 / QA issues"),
        riskRows.map((text, index) => e("div", { key: index, className: "text-[10px] leading-relaxed text-slate-300" }, text))
      ),
      e(LlmDeepHistoryList, { payload, primary })
    )
  );
}

// d7:历史深析放出——payload.items(≤50 条)此前仅取 primary 1 条,其余 614 条库存数据 UI 无门。
function LlmDeepHistoryList({ payload, primary }) {
  const [expanded, setExpanded] = React.useState(false);
  const all = Array.isArray(payload.items) ? payload.items : [];
  const history = all.filter((it) => it && it !== primary && it.id !== primary?.id);
  if (!history.length) return null;
  return e("div", { className: "mt-2 border-t border-white/[0.05] pt-1.5" },
    e("button", {
      type: "button",
      onClick: () => setExpanded((v) => !v),
      className: "flex w-full items-center justify-between text-[9.5px] text-slate-500 hover:text-slate-300",
    },
      `历史深析 ${history.length} 条(共 ${payload.count ?? all.length} 条,当前展示最新判断)`,
      e(ChevronsUpDownIcon, { expanded })
    ),
    expanded && e("div", { className: "mt-1.5 space-y-1" },
      history.slice(0, 8).map((it, index) => e("div", {
        key: it.id ?? index,
        className: "flex items-center justify-between rounded border border-white/[0.04] bg-black/15 px-2 py-1 text-[9.5px] text-slate-400",
      },
        e("span", { className: "truncate" }, String(it.created_at || "").slice(0, 10) || "—"),
        e("span", { className: "tabular-nums text-slate-300" }, it.llm_v6_fit == null ? "—" : scoreText(numberOr(it.llm_v6_fit)))
      )),
      history.length > 8 && e("div", { className: "text-[9px] text-slate-600" }, `…其余 ${history.length - 8} 条`)
    )
  );
}

function ChevronsUpDownIcon({ expanded }) {
  return e("span", { className: "text-[9px] text-slate-600" }, expanded ? "收起 ▲" : "展开 ▼");
}

export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact }) {
  const [dimensions11, setDimensions11] = React.useState(null);
  const [llmDeepAnalysis, setLlmDeepAnalysis] = React.useState(null);
  const [preloadedVideoAnalysisBundles, setPreloadedVideoAnalysisBundles] = React.useState(undefined);
  const [videoEnqueueState, setVideoEnqueueState] = React.useState({ status: "idle", message: "" });
  const [activeRepresentativeVideo, setActiveRepresentativeVideo] = React.useState(null);
  React.useEffect(() => {
    const bundleRecord = recordOr(detailBundle);
    if (bundleRecord.status === "ready") {
      const dimensionsPayload = recordOr(bundleRecord.dimensions11);
      const llmPayload = recordOr(bundleRecord.llm_deep_analysis);
      setDimensions11(dimensionsPayload.status === "missing" || dimensionsPayload.persisted === false ? null : dimensionsPayload);
      setLlmDeepAnalysis(llmPayload.status === "ready" ? llmPayload : null);
      setPreloadedVideoAnalysisBundles(detailBundleAnalysisItems(bundleRecord));
      return;
    }
    setPreloadedVideoAnalysisBundles(undefined);
    if (!apiToken || !item?.id) {
      setDimensions11(null);
      setLlmDeepAnalysis(null);
      return;
    }
    let cancelled = false;
    setDimensions11(null);
    setLlmDeepAnalysis(null);
    void Promise.allSettled([
      getKolPoolDimensions11(apiToken, item.id, { requirePersisted: true }),
      getKolPoolLlmDeepAnalysis(apiToken, item.id),
    ]).then(([dimensionsResult, llmResult]) => {
        if (cancelled) return;
        const dimensionsPayload = dimensionsResult.status === "fulfilled" ? dimensionsResult.value : null;
        const llmPayload = llmResult.status === "fulfilled" ? llmResult.value : null;
        setDimensions11(dimensionsPayload?.status === "missing" ? null : dimensionsPayload);
        setLlmDeepAnalysis(llmPayload?.status === "ready" ? llmPayload : null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, item?.id, detailBundle]);

  React.useEffect(() => {
    setVideoEnqueueState({ status: "idle", message: "" });
    setActiveRepresentativeVideo(null);
  }, [item?.id]);

  if (!item) return null;
  const devices = {
    ...(item.devices || {}),
    lenses: asArray(item.devices?.lenses),
    competitor_brands: asArray(item.devices?.competitor_brands),
  };
  const geoDistribution = asArray(item.geo_distribution);
  const trendHits = asArray(item.trend_hits);
  const representativeVideos = asArray(item.representative_videos);
  const recommendedProductLines = asArray(item.recommended_product_lines);
  const potentialConcerns = asArray(item.potential_concerns);
  const brandCollaborations = asArray(item.brand_collaborations);
  const competitorCollabs = asArray(item.competitor_collabs);
  const loyaltySignals = item.loyalty_signals || {};
  const videoAnalysisVideos = videoAnalysisSources(item, representativeVideos);
  const primaryVideoEvidence = videoAnalysisVideos[0] || null;
  const primaryVideoEvidenceId = evidenceIdOf(primaryVideoEvidence);
  const videoEnqueueBusy = videoEnqueueState.status === "loading" || videoEnqueueState.status === "queued" || videoEnqueueState.status === "already_queued";
  const canEnqueueVideoAnalysis = Boolean(apiToken && item.id && primaryVideoEvidenceId && !videoEnqueueBusy);
  const videoEnqueueLabel =
    videoEnqueueState.status === "loading" ? "入队中…" :
    videoEnqueueState.status === "queued" ? "分析中…" :
    videoEnqueueState.status === "already_analyzed" ? "已分析过" :
    videoEnqueueState.status === "already_queued" ? "已在队列中" :
    "AI深度分析";
  const videoEnqueueTitle =
    videoEnqueueState.message ||
    (primaryVideoEvidenceId
      ? "入队当前主代表作 evidence #" + primaryVideoEvidenceId + "；任务进度会显示在左侧看板"
      : "暂无可分析的 video evidence");
  const handleVideoAnalysisEnqueue = () => {
    if (!apiToken || !item.id || !primaryVideoEvidenceId || videoEnqueueBusy) return;
    setVideoEnqueueState({ status: "loading", message: "正在把 evidence #" + primaryVideoEvidenceId + " 加入深析队列" });
    void enqueueVideoAnalysis(apiToken, item.id, primaryVideoEvidenceId)
      .then((payload) => {
        const status = String(payload?.status || "");
        if (status === "queued") {
          setVideoEnqueueState({ status, message: "已入队；左侧任务进度看板会自动显示" });
        } else if (status === "already_analyzed") {
          setVideoEnqueueState({ status, message: "这条 evidence 已有 final_v1 深析结果" });
        } else if (status === "already_queued") {
          setVideoEnqueueState({ status, message: "这条 evidence 已在队列中，避免重复入队" });
        } else if (status === "budget_denied") {
          setVideoEnqueueState({ status, message: "预算闸门拒绝，本次未入队" });
        } else {
          setVideoEnqueueState({ status: "error", message: payload?.message || payload?.reason || "入队失败" });
        }
      })
      .catch((error) => {
        const message = error?.message ? String(error.message) : "入队失败";
        setVideoEnqueueState({ status: "error", message });
      });
  };
  const dimensions11Dims = dimensions11RadarDims(dimensions11);
  const v6Breakdown = item.v6_breakdown && typeof item.v6_breakdown === "object"
    ? item.v6_breakdown
    : item.score_breakdown && typeof item.score_breakdown === "object"
      ? item.score_breakdown
      : null;
  
  return e(React.Fragment, null,
  e(motion.div, {
    initial: { x: "100%" }, animate: { x: 0 }, exit: { x: "100%" },
    transition: { type: "spring", damping: 28, stiffness: 240 },
    "aria-label": "KOL Pool 详情",
    className: "fixed top-0 right-0 h-full w-[520px] bg-[#0a1020] border-l border-white/[0.08] shadow-2xl z-50 flex flex-col"
  },
    // ─── Header ───
    e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
      e("div", { className: "flex items-start gap-3 mb-2" },
        e(KOLDetailAvatar, { item, size: 44 }),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-1.5" },
            e("h2", { className: "text-[14px] font-semibold text-white truncate" }, item.handle),
            item.linked_main_kol_id && e(BadgeCheck, { size: 12, className: "text-emerald-400 shrink-0" }),
          ),
          e("div", { className: "text-[11px] text-slate-400 truncate" }, item.display_name || ""),
        ),
        // V6 Fit · 紧凑右上
        item.v6_fit != null && e("div", { className: "text-right shrink-0 px-2.5 py-1 rounded-md border border-white/[0.06] bg-white/[0.02]" },
          e("div", { className: "text-[8px] text-slate-500 uppercase tracking-wider leading-none mb-0.5" }, "V6 Fit"),
          e("div", { className: "text-[18px] font-semibold tabular-nums leading-none",
            style: { color: item.v6_fit >= 85 ? "#10b981" : item.v6_fit >= 70 ? "#fbbf24" : "#fb923c" }
          }, item.v6_fit)
        ),
        e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white shrink-0" },
          e(X, { size: 13 })
        )
      ),
      // chips 单独一行
      e("div", { className: "flex items-center gap-1.5 flex-wrap" },
        e(CandidateKindChip, { kind: item.candidate_kind }),
        e(PlatformPill, { platform: item.platform }),
        e(AudienceTypeChip, { type: item.audience_type }),
        e(GeoTierChip, { tier: item.geo_tier }),
        item.country && e("span", { className: "text-[10px] text-slate-500 inline-flex items-center gap-1" },
          e(MapPin, { size: 9 }),
          item.country
        ),
        devices.has_viltrox && e("span", {
          className: "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
          style: { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" }
        }, e(Check, { size: 9 }), "已用 Viltrox"),
        devices.competitor_brands.length > 0 && e("span", {
          className: "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
          style: { background: "rgba(248,113,113,0.15)", color: "#fca5a5" },
          title: "在用友商:" + devices.competitor_brands.join(", ")
        }, e(AlertTriangle, { size: 9 }), "友商用户"),
      ),
      (detailLoading || detailError) && e("div", {
        className: "mt-3 rounded-md border px-2.5 py-2 text-[10.5px] leading-snug",
        style: detailError
          ? { background: "rgba(251,113,133,0.10)", borderColor: "rgba(251,113,133,0.28)", color: "#fecdd3" }
          : { background: "rgba(96,165,250,0.10)", borderColor: "rgba(96,165,250,0.24)", color: "#bfdbfe" }
      }, detailError ? "详情 API 无信号: " + detailError + "；当前显示列表快照。" : "正在读取详情；先显示列表快照。"),
      // ── New-candidate action bar: promote / discard ──
      candidateKindGroup(item.candidate_kind) === "new" && e("div", {
        className: "mt-3 flex items-center gap-2 px-2.5 py-2 rounded-md border",
        style: {
          background: "rgba(168,85,247,0.06)",
          borderColor: "rgba(168,85,247,0.25)",
        }
      },
        e(Sparkles, { size: 12, className: "text-purple-300 shrink-0" }),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "text-[10.5px] text-purple-100" },
            item.candidate_kind === "new_promoted"  ? "新发现 · 高潜候选" :
            item.candidate_kind === "new_validated" ? "新发现 · 已通过基础校验" :
                                                       "新发现 · 校验中"
          ),
          e("div", { className: "text-[9.5px] text-slate-400 truncate" },
            "来源 query: ", e("span", { className: "text-slate-300" }, item.source_query || "—"),
            "  ·  发现于 ", item.discovered_at || "—",
            "  ·  validation_score ", item.validation_score
          ),
        ),
        item.candidate_kind === "new_promoted" && e("button", {
          onClick: ev => ev.stopPropagation(),
          disabled: true,
          className: "flex items-center gap-1 rounded-md px-2.5 py-1 text-[10px] font-medium text-slate-400 shrink-0 cursor-not-allowed",
          style: { background: "rgba(148,163,184,0.14)" }
        }, e(UserPlus, { size: 10 }), "待接入写入")
      )
    ),
    
    // ─── Scroll content ───
    e("div", { className: "flex-1 overflow-y-auto" },
      // ── Bio ──
      item.bio && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, item.bio)
      ),
      
      // ── Why V6 Fit = N? · 速读 4 bullets(规则生成) ──
      v6Breakdown && (() => {
        const b = v6Breakdown;
        const dims = [
          { key: "loyalty",  Icon: Heart, color: "#86efac",
            insight: (v) => "高忠诚度 ×" + fixedOrDash(v) + " · 老粉 " + (loyaltySignals.old_fans_pct ?? "—") + "% · 回复率 " + (loyaltySignals.creator_reply_pct ?? "—") + "%" },
          { key: "geo_match", Icon: Target, color: "#c4b5fd",
            insight: (v) => "海外 Geo 命中 ×" + fixedOrDash(v) + " · " + (COUNTRY_INFO[geoDistribution?.[0]?.country]?.flag || "") + " " + (geoDistribution?.[0]?.country || "—") + " 占 " + Math.round(pctOrZero(geoDistribution?.[0]?.share)) + "%" },
          { key: "trend",    Icon: Flame, color: "#fda4af",
            insight: (v) => "本周流行命中 ×" + fixedOrDash(v) + (trendHits.length ? " · #" + trendHits[0] : " · 未命中") },
          { key: "real_er",  Icon: (v) => v >= 1 ? Check : AlertTriangle, color: (v) => v >= 1 ? "#86efac" : "#fde68a",
            insight: (v) => "Real ER ×" + fixedOrDash(v) + " · 去水后 " + formatPercent(item.real_er_pct, 2) },
          { key: "upgrade",  Icon: Zap, color: "#fde68a",
            insight: (v) => "升级窗口 " + (devices.upgrade_window || "—") + " · 系数 ×" + fixedOrDash(v) },
          { key: "industry", Icon: Video, color: "#c4b5fd",
            insight: (v) => "行业 Tier " + (item.industry_tier || "—") + " · " + (item.industry_label || "") + " ×" + fixedOrDash(v) },
          { key: "platform_native", Icon: Activity, color: "#94a3b8",
            insight: (v) => "平台原生度 ×" + fixedOrDash(v) },
          { key: "price_match", Icon: ShoppingBag, color: "#94a3b8",
            insight: (v) => "价位匹配 ×" + fixedOrDash(v) },
        ];
        // 取偏离 1 最远的 top 3(最影响分数的维度)
        const scored = dims
          .filter(d => b[d.key] != null)
          .map(d => ({ ...d, value: b[d.key], deviation: Math.abs(b[d.key] - 1) }))
          .sort((a, x) => x.deviation - a.deviation)
          .slice(0, 3);
        // 第 4 条:风险/机会(永远显示一条,平衡感)
        let risk;
        if (competitorCollabs.length > 0) {
          risk = { Icon: Shield, color: "#fca5a5", text: "友商合作历史 · " + competitorCollabs.map(c => typeof c === "string" ? c : (c.brand || "")).join(", ") };
        } else if (devices.competitor_brands.length > 0) {
          risk = { Icon: AlertTriangle, color: "#fde68a", text: "当前在用友商 · " + devices.competitor_brands.join(", ") };
        } else if (potentialConcerns.length > 0) {
          risk = { Icon: AlertTriangle, color: "#fde68a", text: concernLabel(potentialConcerns[0]) };
        } else {
          risk = { Icon: Check, color: "#86efac", text: "无友商合作 · 合作记录干净" };
        }
        return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
          e("div", { className: "flex items-center justify-between mb-2" },
            e("div", { className: "flex items-center gap-1.5" },
              e(Sparkles, { size: 11, className: "text-purple-400" }),
              e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "为什么 V6 Fit = " + item.v6_fit + "?")
            ),
            e("span", { className: "text-[9px] text-slate-600" }, "速读 · 看完整公式 ↓")
          ),
          e("div", { className: "space-y-1.5" },
            scored.map((d, i) => {
              const IconComp = typeof d.Icon === "function" && !d.Icon.$$typeof ? d.Icon(d.value) : d.Icon;
              const color = typeof d.color === "function" ? d.color(d.value) : d.color;
              return e("div", { key: i, className: "flex items-start gap-2 text-[11px] leading-snug" },
                e("span", { className: "shrink-0 inline-flex items-center justify-center w-4 h-4", style: { color } }, e(IconComp, { size: 11 })),
                e("span", { className: "text-slate-300" }, d.insight(d.value))
              );
            }),
            e("div", { className: "flex items-start gap-2 text-[11px] leading-snug pt-1.5 mt-1 border-t border-white/[0.04]" },
              e("span", { className: "shrink-0 inline-flex items-center justify-center w-4 h-4", style: { color: risk.color } }, e(risk.Icon, { size: 11 })),
              e("span", { className: "text-slate-300" }, risk.text)
            )
          )
        );
      })(),
      
      // ── 4-card 2x2 grid: Real ER / Geo / Loyalty / Trend ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06] grid grid-cols-2 gap-2" },
        // Real Engagement
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
          e("div", { className: "flex items-center gap-1.5 mb-1.5" },
            e(Heart, { size: 10, className: "text-rose-400" }),
            e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Real Engagement")
          ),
          e("div", { className: "flex items-baseline gap-1.5" },
            e("span", { className: "text-xl font-light text-white tabular-nums" }, formatPercent(item.real_er_pct, 2)),
            item.er_calibration != null && e("span", { className: "text-[10px] text-rose-400 tabular-nums" }, item.er_calibration + "%")
          ),
          e("div", { className: "text-[9px] text-slate-500" }, 
            "原始 ", formatPercent(item.engagement_rate, 1), " · 去 ", Math.abs(item.er_calibration || 0), "% 水分"
          )
        ),
        // Audience Type · HHI
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
          e("div", { className: "flex items-center gap-1.5 mb-1.5" },
            e(MapPin, { size: 10, className: "text-amber-400" }),
            e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Audience · HHI")
          ),
          e("div", { className: "flex items-baseline gap-1.5" },
            e(AudienceTypeChip, { type: item.audience_type }),
            e("span", { className: "text-[10px] text-slate-500 tabular-nums" }, "HHI " + fixedOrDash(item.hhi || 0, 2))
          ),
          e("div", { className: "text-[9px] text-slate-500 mt-1" }, 
            item.audience_type === "Global" ? "适合国际展会 / 跨市场" :
            item.audience_type === "Regional" ? "适合区域 campaign" :
            "适合本地线下活动"
          )
        ),
        // Loyalty
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
          e("div", { className: "flex items-center gap-1.5 mb-1.5" },
            e(Shield, { size: 10, className: "text-emerald-400" }),
            e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Loyalty Depth")
          ),
          e("div", { className: "flex items-baseline gap-1.5" },
            e("span", { className: "text-xl font-light text-white tabular-nums" }, fixedOrDash(item.loyalty_score, 2)),
          ),
          e("div", { className: "text-[9px] text-slate-500" }, 
            loyaltySignals.old_fans_pct != null ? "老粉 " + loyaltySignals.old_fans_pct + "% · 回复率 " + loyaltySignals.creator_reply_pct + "%" : "—"
          )
        ),
        // Trend Resonance
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
          e("div", { className: "flex items-center gap-1.5 mb-1.5" },
            e(Flame, { size: 10, className: "text-rose-400" }),
            e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Trend Resonance")
          ),
          e("div", { className: "flex items-baseline gap-1.5" },
            e("span", { className: "text-xl font-light text-white tabular-nums" }, item.trend_resonance != null ? pctOrZero(item.trend_resonance).toFixed(0) : "—"),
            e("span", { className: "text-[10px] text-slate-500" }, "/ 100")
          ),
          e("div", { className: "text-[9px] text-slate-500" }, 
            item.trend_resonance == null ? "Trend 数据待接入" : trendHits.length > 0 ? "命中 " + trendHits.length + " 个真实 trend" : "无 trend 信号"
          )
        )
      ),
      
      // ── 11 维度雷达: persisted backend dimensions_11_json only ──
      dimensions11Dims.length > 0 && e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center justify-between mb-3" },
          e("div", { className: "flex items-center gap-1.5" },
            e(Target, { size: 11, className: "text-purple-400" }),
            e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "11 维度评估")
          ),
          e("span", { className: "text-[9px] text-slate-500" },
            "规则画像 · ",
            scoreText(dimensions11?.overall_score),
            " · conf ",
            fixedOrDash(recordOr(dimensions11?.confidence).overall, 2)
          )
        ),
        (() => {
          const dims = dimensions11Dims;
          return e("div", { className: "flex items-center gap-4" },
            e("svg", { width: 160, height: 160, viewBox: "-100 -100 200 200", className: "shrink-0" },
              [20, 40, 60, 80].map(r => e("circle", { key: r, cx: 0, cy: 0, r, className: "radar-bg", fill: "none", stroke: "rgba(255,255,255,0.06)" })),
              dims.map((d, i) => {
                const angle = (i / dims.length) * 2 * Math.PI - Math.PI / 2;
                return e("line", {
                  key: i, x1: 0, y1: 0,
                  x2: 80 * Math.cos(angle), y2: 80 * Math.sin(angle),
                  stroke: "rgba(255,255,255,0.06)", strokeWidth: 1
                });
              }),
              e("polygon", {
                fill: "rgba(168,85,247,0.18)",
                stroke: "#a855f7",
                strokeWidth: 1.5,
                points: dims.map((d, i) => {
                  const angle = (i / dims.length) * 2 * Math.PI - Math.PI / 2;
                  const r = (scoreValue(d.value) / 100) * 80;
                  return `${r * Math.cos(angle)},${r * Math.sin(angle)}`;
                }).join(" ")
              })
            ),
            e("div", { className: "flex-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]" },
              dims.map((d, i) => e("div", { key: i, className: "flex items-center justify-between" },
                e("span", { className: "text-slate-400" }, d.label),
                e("span", { className: "text-white tabular-nums font-medium" }, scoreText(d.value))
              ))
            )
          );
        })()
      ),
      e(LlmDeepAnalysisPanel, { payload: llmDeepAnalysis }),
      
      // ── 联系方式 & 代表视频 ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-2" },
          e(Send, { size: 11, className: "text-cyan-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "联系方式 & 代表作")
        ),
        // 联系方式
        e("div", { className: "space-y-1 mb-3" },
          e("div", { className: "flex items-center gap-2 text-[11px]" },
            e("span", { className: "text-slate-500 w-[40px]" }, "邮箱"),
            item.email
              ? e("span", { className: "text-cyan-300" }, item.email)
              : e("span", { className: "text-slate-500 italic" }, "未收集 · 邀请时需先添加"),
            item.email && e(CopyEmailButton, { email: item.email })
          ),
          e("div", { className: "flex items-center gap-2 text-[11px]" },
            e("span", { className: "text-slate-500 w-[40px]" }, "主页"),
            item.profile_url
              ? e("a", { 
                  href: item.profile_url, target: "_blank", rel: "noreferrer",
                  className: "text-cyan-300 hover:text-cyan-200 truncate flex-1"
                }, item.profile_url.replace("https://", ""))
              : e("span", { className: "text-slate-500" }, "—")
          )
        ),
        // 代表作视频
        representativeVideos.length > 0 && e("div", null,
          e("div", { className: "text-[10px] text-slate-500 mb-1.5" }, "代表作"),
          e("div", { className: "grid grid-cols-3 gap-1.5" },
            representativeVideos.map((v, i) => e(RepresentativeVideoCard, {
              key: v.evidence_id || v.watch_url || v.url || v.title || i,
              video: v,
              index: i,
              onOpen: setActiveRepresentativeVideo,
            }))
          )
        )
      ),
      e(KOLVideoAnalysisPanel, { apiToken, videos: videoAnalysisVideos, preloadedBundles: preloadedVideoAnalysisBundles }),
      devices.camera_body && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-2" },
          e(Camera, { size: 11, className: "text-slate-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "当前设备 & 升级机会")
        ),
        // Camera body
        e("div", { className: "flex items-center gap-2 mb-2" },
          e("span", { className: "text-[10px] text-slate-500" }, "机身"),
          e("span", { className: "text-[12px] text-white font-medium" }, devices.camera_body)
        ),
        // Lenses
        devices.lenses.length > 0 && e("div", { className: "space-y-1 mb-2" },
          e("div", { className: "text-[10px] text-slate-500 mb-1" }, "在用镜头"),
          devices.lenses.map((l, i) => {
            const bi = BRAND_TIER[l.brand] || { color: "#94a3b8", label: l.brand };
            return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
              e("span", {
                className: "px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider",
                style: { 
                  background: bi.tier === "viltrox" ? "rgba(168,85,247,0.18)" 
                           : bi.tier === "competitor" ? "rgba(248,113,113,0.15)" 
                           : "rgba(148,163,184,0.12)",
                  color: bi.color
                }
              }, bi.label),
              e("span", { className: "text-white" }, l.model),
              l.type === "viltrox" && e("span", { className: "ml-auto text-[9px] text-emerald-400 inline-flex items-center gap-0.5" }, e(Check, { size: 9 }), "已用")
            );
          })
        ),
        // Upgrade window
        e("div", { className: "flex items-center gap-2 mt-2 pt-2 border-t border-white/[0.04]" },
          e("span", { className: "text-[10px] text-slate-500" }, "Upgrade 窗口"),
          e("span", { 
            className: "px-2 py-0.5 rounded text-[10px] font-medium",
            style: {
              background: devices.upgrade_window === "very high" ? "rgba(16,185,129,0.2)"
                       : devices.upgrade_window === "high" ? "rgba(16,185,129,0.15)"
                       : devices.upgrade_window === "medium" ? "rgba(251,191,36,0.15)"
                       : "rgba(100,116,139,0.15)",
              color: devices.upgrade_window === "very high" ? "#34d399"
                   : devices.upgrade_window === "high" ? "#86efac"
                   : devices.upgrade_window === "medium" ? "#fde68a"
                   : "#94a3b8"
            }
          }, devices.upgrade_window),
          e("span", { className: "text-[10px] text-slate-500" }, "× 系数 " + fixedOrDash(item.upgrade_factor || 1, 2))
        )
      ),
      
      // ── Geo distribution ──
      geoDistribution.length > 0 && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-2" },
          e(Globe2, { size: 11, className: "text-cyan-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "粉丝地理分布 · 估算 Reach")
        ),
        e("div", { className: "space-y-1.5" },
          geoDistribution.map((g, i) => {
            const cInfo = getCountryInfo(g.country) || { code: g.country, flag: "·", name: g.country, tier: "?" };
            const reach = item.estimated_country_reach?.[cInfo.code] || item.estimated_country_reach?.[g.country];
            const sharePct = pctOrZero(g.share);
            return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
              e("span", { style: { fontSize: 12 } }, cInfo.flag),
              e("span", { className: "text-white font-medium w-[28px]" }, cInfo.code),
              e(GeoTierChip, { tier: cInfo.tier }),
              e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
                e("div", { className: "geo-bar-fill", style: {
                  width: sharePct + "%",
                  background: cInfo.tier === "A" ? "#10b981" : cInfo.tier === "B" ? "#fbbf24" : "#64748b"
                }})
              ),
              e("span", { className: "text-slate-300 tabular-nums w-[40px] text-right" }, sharePct.toFixed(0) + "%"),
              reach && e("span", { className: "text-slate-500 tabular-nums text-[10px] ml-auto" }, "~" + formatNumber(reach) + " reach")
            );
          })
        )
      ),
      
      // ── V6 Fit Breakdown ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-2" },
          e(Layers, { size: 11, className: "text-purple-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "V6 Fit 公式 Breakdown")
        ),
        e("div", { className: "space-y-1.5" },
          [
            { label: "Base Match (内容)",        value: v6Breakdown?.base ?? item.v6_fit,         suffix: "" },
            { label: "× Industry Tier",          value: v6Breakdown?.industry,     suffix: "" },
            { label: "× Upgrade Factor",         value: v6Breakdown?.upgrade ?? item.upgrade_factor,      suffix: "" },
            { label: "× Geo Match (海外加权)",    value: v6Breakdown?.geo_match ?? item.geo_match,    suffix: "" },
            { label: "× Real ER",                value: v6Breakdown?.real_er,      suffix: "" },
            { label: "× Loyalty Depth",          value: v6Breakdown?.loyalty,      suffix: "" },
            { label: "× Trend Resonance",        value: v6Breakdown?.trend ?? item.trend_resonance,        suffix: "" },
            { label: "× Platform Native",        value: v6Breakdown?.platform_native, suffix: "" },
            { label: "× Price Match",            value: v6Breakdown?.price_match,  suffix: "" },
            { label: "× Network Boost",          value: v6Breakdown?.network,      suffix: "" },
          ].map((m, i) => {
            const val = m.value;
            const isBase = i === 0;
            const isPositive = !isBase && val >= 1.0;
            const isNegative = !isBase && val < 1.0;
            return e("div", { key: i, className: "flex items-center justify-between text-[11px]" },
              e("span", { className: "text-slate-400" }, m.label),
              e("span", { 
                className: "tabular-nums font-medium",
                style: { color: isBase ? "#fff" : isPositive ? "#86efac" : isNegative ? "#fca5a5" : "#fff" }
              }, isBase ? (val ?? "—") : fixedOrDash(val))
            );
          }),
          v6Breakdown?.competitor_decay < 0 && e("div", { className: "flex items-center justify-between text-[11px]" },
            e("span", { className: "text-slate-400" }, "− Competitor Decay"),
            e("span", { className: "tabular-nums font-medium text-rose-400" }, v6Breakdown.competitor_decay)
          ),
          e("div", { className: "border-t border-white/[0.06] pt-1.5 mt-1 flex items-center justify-between" },
            e("span", { className: "text-[11px] text-slate-300 font-medium" }, "= Final V6 Fit"),
            e("span", { className: "text-[14px] font-semibold tabular-nums",
              style: { color: item.v6_fit >= 85 ? "#10b981" : item.v6_fit >= 70 ? "#fbbf24" : "#fb923c" }
            }, scoreText(item.v6_fit))
          )
        )
      ),
      
      // ── Trend hits ──
      trendHits.length > 0 && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-2" },
          e(Flame, { size: 11, className: "text-rose-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "本周 Trend 命中")
        ),
        e("div", { className: "flex flex-wrap gap-1" },
          trendHits.map((t, i) => e("span", {
            key: i,
            className: "px-2 py-0.5 rounded text-[10px] border",
            style: { background: "rgba(239,68,68,0.08)", borderColor: "rgba(239,68,68,0.2)", color: "#fda4af" }
          }, "#" + t))
        )
      ),
      
      // ── Viltrox Fit reason ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 mb-1.5" },
          e(Sparkles, { size: 11, className: "text-purple-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Viltrox 适配判断")
        ),
        item.viltrox_fit_reason
          ? e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, item.viltrox_fit_reason)
          : e("p", { className: "text-[11px] text-slate-500 leading-relaxed" }, "本地适配原因字段为空 · 等待 enrichment 或人工补全")
      ),
      
      // ── Recommended products ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "推荐产品线"),
        recommendedProductLines.length > 0
          ? e("div", { className: "flex flex-wrap gap-1.5" }, recommendedProductLines.map((p, i) => e("span", {
            key: i,
            className: "px-2 py-1 rounded-md border text-[10px]",
            style: { background: "rgba(168,85,247,0.08)", borderColor: "rgba(168,85,247,0.3)", color: "#c4b5fd" }
          }, p)))
          : e("div", { className: "text-[11px] text-slate-500" }, "本地推荐字段为空 · 等待 enrichment 或历史合作导入")
      ),
      
      // ── Concerns ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-amber-300 mb-2" },
          e(AlertTriangle, { size: 10 }), "风险点"
        ),
        potentialConcerns.length > 0
          ? e("div", { className: "space-y-1" }, potentialConcerns.map((c, i) => e("div", { 
            key: i,
            className: "text-[11px] text-slate-300 pl-3 border-l-2 border-amber-400/40 py-0.5"
          }, concernLabel(c))))
          : e("div", { className: "text-[11px] text-slate-500" }, "暂无结构化风险点 · 本地风险字段为空")
      ),
      
      // ── Brand history ──
      e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("div", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2" }, "品牌合作历史"),
        brandCollaborations.length > 0
          ? e("div", { className: "space-y-1.5" }, brandCollaborations.map((b, i) => {
            const isCompetitor = competitorCollabs.includes(b.brand);
            return e("div", { key: i, className: "flex items-center gap-2 text-[10px]" },
              e("span", { 
                className: "shrink-0 px-1.5 py-0.5 rounded text-[9px] font-medium",
                style: isCompetitor 
                  ? { background: "rgba(248,113,113,0.15)", color: "#f87171" }
                  : b.brand === "Viltrox"
                  ? { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" }
                  : { background: "rgba(100,116,139,0.15)", color: "#94a3b8" }
              }, b.brand),
              e("span", { className: "text-slate-500" }, b.year + " · "),
              e("span", { className: "text-slate-300 flex-1 text-[11px]" }, b.deal),
              isCompetitor && e("span", { className: "text-[9px] text-rose-400 font-medium" }, "友商")
            );
          }))
          : e("div", { className: "text-[11px] text-slate-500" }, "本地合作历史为空 · 等待历史合作导入")
      ),
    ),
    
    // ─── Footer actions ───
    e("div", { className: "px-5 py-3 border-t border-white/[0.06]" },
      // 主操作 3 按钮
      e("div", { className: "flex items-center gap-2 mb-2" },
        e("button", {
          onClick: () => onToggleMyList?.(item.id),
          title: "我的收藏(My KOL 归宿)· 持久化保存",
          className: "flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[11px] font-medium transition-colors",
          style: inMyList
            ? { background: "rgba(251,191,36,0.15)", color: "#fde68a", border: "1px solid rgba(251,191,36,0.35)" }
            : { background: "rgba(255,255,255,0.04)", color: "#e2e8f0", border: "1px solid rgba(255,255,255,0.1)" }
        }, e(Star, { size: 11, style: inMyList ? { fill: "#fbbf24" } : {} }), inMyList ? "已收藏" : "加入收藏"),
        e("button", {
          onClick: () => onContact?.(item),
          title: "打开本地联系模板: 不发送邮件、不调用 provider",
          className: "flex-1 flex items-center justify-center gap-1.5 rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-2 text-[11px] font-medium text-white"
        }, e(Send, { size: 11 }),
          item.email ? "发起合作邀请" : "添加联系方式"
        ),
        !item.linked_main_kol_id && e("button", {
          disabled: true,
          className: "flex-1 flex cursor-not-allowed items-center justify-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/[0.05] px-3 py-2 text-[11px] text-emerald-400/70",
          title: "待接入: 需要主表写入 API 和权限校验"
        }, e(Link2, { size: 11 }), "入主表 · 待接入"),
      ),
      // 次操作 icons
      e("div", { className: "flex items-center justify-center gap-1.5" },
        e("button", {
          disabled: !canEnqueueVideoAnalysis,
          onClick: handleVideoAnalysisEnqueue,
          className: "flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] transition-colors " + (
            canEnqueueVideoAnalysis
              ? "border-cyan-400/25 bg-cyan-400/[0.07] text-cyan-200 hover:bg-cyan-400/[0.12]"
              : "cursor-not-allowed border-white/[0.06] text-slate-500 opacity-70"
          ),
          title: videoEnqueueTitle
        }, e(RefreshCw, { size: 10, className: videoEnqueueState.status === "loading" ? "animate-spin" : "" }), videoEnqueueLabel),
        item.profile_url && e("a", {
          href: item.profile_url, target: "_blank", rel: "noreferrer",
          className: "flex items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-[10px] text-slate-400 hover:bg-white/[0.04] hover:text-white"
        }, e(ExternalLink, { size: 10 }), "打开主页"),
        e("button", {
          disabled: true,
          className: "flex cursor-not-allowed items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-[10px] text-slate-500 opacity-70",
          title: "待接入: 更多操作需要明确菜单项和权限"
        }, e(MoreHorizontal, { size: 10 }), "更多 · 待接入"),
      ),
      videoEnqueueState.message && e("div", {
        className: "mt-1 text-center text-[10px] leading-snug " + (
          videoEnqueueState.status === "error" || videoEnqueueState.status === "budget_denied"
            ? "text-rose-300"
            : videoEnqueueState.status === "queued" || videoEnqueueState.status === "already_queued"
              ? "text-cyan-200"
              : "text-slate-400"
        )
      }, videoEnqueueState.message)
    )
  ),
    activeRepresentativeVideo && e(RepresentativeVideoPlayerModal, {
      video: activeRepresentativeVideo,
      onClose: () => setActiveRepresentativeVideo(null),
    })
  );
}
