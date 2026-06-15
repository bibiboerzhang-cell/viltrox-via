// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Check, Link2, Shield, Sparkles, Video } from "lucide-react";
import { KPAvatar } from "./KPAvatar";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";
import { enqueueVideoAnalysis, getKolPoolContentFit, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis } from "../../../../services/vkpi/kolPool-api";
import { getKolMemory } from "../../../../services/vkpi/kolMemory-api";
import { proxiedImageUrl, proxiedVideoUrl } from "../../shared/mediaProxy";
import { asArray, compactText, fixedOrDash, numberOr, recordOr, scoreText, scoreValue } from "./KOLDetailDrawer.helpers";
import {
  KOLDrawerContactAndVideos,
  KOLDrawerContentFit,
  KOLDrawerDevices,
  KOLDrawerFooter,
  KOLDrawerGeoDistribution,
  KOLDrawerHeader,
  KOLDrawerMemorySection,
  KOLDrawerMetricGrid,
  KOLDrawerRadar11,
  KOLDrawerTextSections,
  KOLDrawerTrendHits,
  KOLDrawerV6Breakdown,
  KOLDrawerWhyFitCard,
} from "./KOLDetailDrawerSections";

const e = React.createElement;

// d1:真复制按钮(原按钮有 title 无 onClick,点击无声失败——B2 全页唯一假按钮)
export function CopyEmailButton({ email }: any) {
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

function detailAvatarUrl(item: any) {
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

export function KOLDetailAvatar({ item, size = 44 }: any) {
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

function flexibleTextList(value: any, maxItems = 3) {
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

function maxScore(record: any) {
  const values = Object.values(recordOr(record)).map((value: any) => numberOr(value)).filter((value: any) => value != null);
  return values.length ? Math.max(...values) : 0;
}

function dimensions11RadarDims(payload: any) {
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

function videoAnalysisSources(item: any, representativeVideos: any) {
  const seen = new Set();
  return [...asArray(item.video_evidence), ...representativeVideos].filter((video) => {
    if (!video || typeof video !== "object") return false;
    const key = String(video.evidence_id || video.id || video.content_url || video.url || video.title || "");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function cacheEntryOrNull(value: any) {
  const record = recordOr(value);
  return Object.keys(record).length ? record : null;
}

function detailBundleAnalysisItems(detailBundle: any) {
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

function detailBundleAnalysisSummary(detailBundle: any) {
  const summary = recordOr(recordOr(recordOr(detailBundle).video_analysis).summary);
  return Object.keys(summary).length ? summary : null;
}

function evidenceIdOf(video: any) {
  if (!video || typeof video !== "object") return null;
  const value = video.evidence_id ?? video.id;
  const id = Number(value);
  return Number.isFinite(id) && id > 0 ? id : null;
}

function videoString(video: any, keys: any, fallback = "") {
  const source = recordOr(video);
  for (const key of keys) {
    const value = source[key];
    if (value !== null && value !== undefined && String(value).trim()) return String(value).trim();
  }
  return fallback;
}

function hostFromUrl(url: any) {
  try {
    const raw = String(url || "").trim();
    if (!raw) return "";
    const normalized = raw.includes("://") ? raw : `https://${raw}`;
    return new URL(normalized).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function parseYoutubeVideoId(url: any) {
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

function normalizedVideoPlatform(video: any) {
  const explicit = videoString(video, ["platform"]).toLowerCase();
  if (explicit) return explicit;
  const url = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(url);
  if (host.includes("youtube.com") || host.includes("youtu.be")) return "youtube";
  if (host.includes("instagram.com")) return "instagram";
  if (host.includes("tiktok.com")) return "tiktok";
  return "media";
}

function youtubeIdForVideo(video: any) {
  const platform = normalizedVideoPlatform(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const host = hostFromUrl(watchUrl);
  if (platform !== "youtube" && !host.includes("youtube.com") && !host.includes("youtu.be")) return "";
  return videoString(video, ["youtube_video_id"]) || parseYoutubeVideoId(watchUrl);
}

function youtubeEmbedUrl(videoId: any) {
  const id = String(videoId || "").trim();
  if (!id) return "";
  const origin = typeof window !== "undefined" && window.location?.origin
    ? `&origin=${encodeURIComponent(window.location.origin)}`
    : "";
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1&modestbranding=1${origin}`;
}

export function RepresentativeVideoCard({ video, index, onOpen }: any) {
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

function RepresentativeVideoPlayerModal({ video, onClose }: any) {
  const title = videoString(video, ["title", "video_title"], "代表作");
  const thumbnail = proxiedImageUrl(videoString(video, ["best_thumbnail", "thumbnail_url", "youtube_thumbnail_url"]));
  const cachedVideoUrl = proxiedVideoUrl(videoString(video, ["cached_video_url"]));
  const youtubeVideoId = youtubeIdForVideo(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const platform = normalizedVideoPlatform(video);
  const embedSrc = youtubeEmbedUrl(youtubeVideoId);

  React.useEffect(() => {
    const handleKey = (event: any) => {
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
    // P11:YouTube iframe 仅在平台确为 youtube 时渲染。IG/TikTok 即便误带到 youtubeVideoId,
    // 也绝不塞进 YouTube 播放器(否则黑屏)——无 R2 缓存时落到下方「打开原帖」兜底。
    : (platform === "youtube" && youtubeVideoId)
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

function LlmDeepAnalysisPanel({ payload }: any) {
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
function LlmDeepHistoryList({ payload, primary }: any) {
  const [expanded, setExpanded] = React.useState(false);
  const all = Array.isArray(payload.items) ? payload.items : [];
  const history = all.filter((it: any) => it && it !== primary && it.id !== primary?.id);
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
      history.slice(0, 8).map((it: any, index: number) => e("div", {
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

function ChevronsUpDownIcon({ expanded }: any) {
  return e("span", { className: "text-[9px] text-slate-600" }, expanded ? "收起 ▲" : "展开 ▼");
}

export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact }: any) {
  const [dimensions11, setDimensions11] = React.useState<any>(null);
  const [llmDeepAnalysis, setLlmDeepAnalysis] = React.useState<any>(null);
  const [preloadedVideoAnalysisBundles, setPreloadedVideoAnalysisBundles] = React.useState<any>(undefined);
  const [videoAnalysisSummary, setVideoAnalysisSummary] = React.useState<any>(null);
  const [videoEnqueueState, setVideoEnqueueState] = React.useState<any>({ status: "idle", message: "" });
  const [activeRepresentativeVideo, setActiveRepresentativeVideo] = React.useState<any>(null);
  // 地基B 内容契合深析(content_fit_v1):默认只读缓存;点击才按需触发深析(不烧 LLM 直到点击)。
  const [contentFit, setContentFit] = React.useState<any>(null);
  const [contentFitBusy, setContentFitBusy] = React.useState(false);
  const [contentFitError, setContentFitError] = React.useState("");
  // W3 长期记忆(纯聚合,显式独立于 V6 Fit · 不影响排序;snapshot 不含任何 fit/score 字段)。
  const [kolMemory, setKolMemory] = React.useState<any>(null);
  React.useEffect(() => {
    const bundleRecord = recordOr(detailBundle);
    if (bundleRecord.status === "ready") {
      const dimensionsPayload = recordOr(bundleRecord.dimensions11);
      const llmPayload = recordOr(bundleRecord.llm_deep_analysis);
      setDimensions11(dimensionsPayload.status === "missing" || dimensionsPayload.persisted === false ? null : dimensionsPayload);
      setLlmDeepAnalysis(llmPayload.status === "ready" ? llmPayload : null);
      setPreloadedVideoAnalysisBundles(detailBundleAnalysisItems(bundleRecord));
      setVideoAnalysisSummary(detailBundleAnalysisSummary(bundleRecord));
      return;
    }
    setPreloadedVideoAnalysisBundles(undefined);
    setVideoAnalysisSummary(null);
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

  // 内容契合深析:开抽屉先只读已有缓存(不烧 LLM);无缓存则留待用户点击触发。
  React.useEffect(() => {
    setContentFit(null);
    setContentFitError("");
    setContentFitBusy(false);
    if (!apiToken || !item?.id) return;
    let cancelled = false;
    void getKolPoolContentFit(apiToken, item.id)
      .then((payload) => {
        if (cancelled) return;
        setContentFit(payload && payload.state === "ready" ? payload : null);
      })
      .catch(() => {
        if (!cancelled) setContentFit(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, item?.id]);

  // W3 长期记忆:开抽屉纯读聚合快照(不烧 LLM、零触评分)。失败静默(记忆是增益,非阻塞)。
  React.useEffect(() => {
    setKolMemory(null);
    if (!apiToken || !item?.id) return;
    let cancelled = false;
    void getKolMemory(apiToken, item.id)
      .then((payload) => {
        if (cancelled) return;
        setKolMemory(payload && typeof payload === "object" ? payload : null);
      })
      .catch(() => {
        if (!cancelled) setKolMemory(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, item?.id]);

  const handleContentFitAnalyze = (force = false) => {
    if (!apiToken || !item?.id || contentFitBusy) return;
    setContentFitBusy(true);
    setContentFitError("");
    void getKolPoolContentFit(apiToken, item.id, { analyze: true, force })
      .then((payload) => {
        if (payload && payload.state === "ready") {
          setContentFit(payload);
        } else {
          setContentFit(null);
          setContentFitError(
            payload?.status === "insufficient_evidence"
              ? "该 KOL 暂无可用视频分析证据,无法做内容契合深析(不杜撰)。"
              : "深析未产出(可能 LLM 暂不可达),请稍后重试。",
          );
        }
      })
      .catch(() => setContentFitError("深析请求失败,请稍后重试。"))
      .finally(() => setContentFitBusy(false));
  };

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
    e(KOLDrawerHeader, { item, devices, detailLoading, detailError, onClose }),

    // ─── Scroll content ───
    e("div", { className: "flex-1 overflow-y-auto" },
      // ── Bio ──
      item.bio && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
        e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, item.bio)
      ),
      
      // ── Why V6 Fit = N? · 速读 4 bullets(规则生成) ──
      v6Breakdown && e(KOLDrawerWhyFitCard, { v6Breakdown, loyaltySignals, geoDistribution, trendHits, item, devices, competitorCollabs, potentialConcerns }),

      // ── 4-card 2x2 grid: Real ER / Geo / Loyalty / Trend ──
      e(KOLDrawerMetricGrid, { item, loyaltySignals, trendHits }),

      // ── 11 维度雷达: persisted backend dimensions_11_json only ──
      dimensions11Dims.length > 0 && e(KOLDrawerRadar11, { dims: dimensions11Dims, dimensions11 }),
      e(LlmDeepAnalysisPanel, { payload: llmDeepAnalysis }),

      // ── 长期记忆(W3)· 显式独立于 V6 Fit · 不影响排序 ──
      // 红线:本区块纯渲染聚合记忆,绝不渲染任何 viltrox/v6_fit 数值。
      e(KOLDrawerMemorySection, { kolMemory }),

      // ── 联系方式 & 代表视频 ──
      e(KOLDrawerContactAndVideos, { item, representativeVideos, onOpenVideo: setActiveRepresentativeVideo }),
      e(KOLVideoAnalysisPanel, { apiToken, videos: videoAnalysisVideos, preloadedBundles: preloadedVideoAnalysisBundles, summary: videoAnalysisSummary }),
      // 地基B:内容契合深析(content_fit_v1)——基于视频画面/故事 + 评论的适配判断(胜过粉丝数)。
      e(KOLDrawerContentFit, { apiToken, item, contentFit, contentFitBusy, contentFitError, onAnalyze: handleContentFitAnalyze }),
      e(KOLDrawerDevices, { item, devices }),

      // ── Geo distribution ──
      geoDistribution.length > 0 && e(KOLDrawerGeoDistribution, { item, geoDistribution }),

      // ── V6 Fit Breakdown ──
      e(KOLDrawerV6Breakdown, { item, v6Breakdown }),

      // ── Trend hits ──
      trendHits.length > 0 && e(KOLDrawerTrendHits, { trendHits }),

      // ── Viltrox 适配判断 / 推荐产品线 / 风险点 / 品牌合作历史 ──
      e(KOLDrawerTextSections, { item, recommendedProductLines, potentialConcerns, brandCollaborations, competitorCollabs }),
    ),
    
    // ─── Footer actions ───
    e(KOLDrawerFooter, {
      item, inMyList, onToggleMyList, onContact,
      canEnqueueVideoAnalysis, videoEnqueueLabel, videoEnqueueTitle, videoEnqueueState,
      onEnqueueVideoAnalysis: handleVideoAnalysisEnqueue,
    })
  ),
    activeRepresentativeVideo && e(RepresentativeVideoPlayerModal, {
      video: activeRepresentativeVideo,
      onClose: () => setActiveRepresentativeVideo(null),
    })
  );
}
