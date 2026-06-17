// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Check, Link2, Share2, Shield, Sparkles, Video } from "lucide-react";
import { KPAvatar } from "./KPAvatar";
import { AnalysisCard, KOLVideoAnalysisPanel, type AnalysisBundle } from "./KOLVideoAnalysisPanel";
import { ShareKolModal } from "../../shared/ShareKolModal";
import { enqueueAllKolVideos, enqueueVideoAnalysis, getKolPoolAccountDossier, getKolPoolContentFit, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis } from "../../../../services/vkpi/kolPool-api";
import { getKolMemory } from "../../../../services/vkpi/kolMemory-api";
import { GoaffproLinkSection } from "../../shared/GoaffproLinkSection";
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

// E1:点代表作视频 → 在播放器下方内联看「该条」逐视频深析。
// 纯只读 preloaded final_v1 缓存(detailBundle.video_analysis.items),先按 evidenceIdOf 精确匹配;
// 代表作卡有时不带 evidence_id(loose key 回退到 url),故再按 watch_url/url/content_url 兜底匹配。
// 命中复用 KOLVideoAnalysisPanel 的 AnalysisCard;未命中显示「该视频暂无深析」。绝不触评分。
function videoUrlKey(video: any) {
  return videoString(video, ["watch_url", "url", "content_url"]).toLowerCase();
}

function matchAnalysisBundle(video: any, bundles: any): AnalysisBundle | null {
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

function RepresentativeVideoPlayerModal({ video, onClose, bundles = null, apiToken = "" }: any) {
  const title = videoString(video, ["title", "video_title"], "代表作");
  const thumbnail = proxiedImageUrl(videoString(video, ["best_thumbnail", "thumbnail_url", "youtube_thumbnail_url"]));
  const cachedVideoUrl = proxiedVideoUrl(videoString(video, ["cached_video_url"]));
  const youtubeVideoId = youtubeIdForVideo(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  const platform = normalizedVideoPlatform(video);
  const embedSrc = youtubeEmbedUrl(youtubeVideoId);
  // 命中条件:既能从该视频取到 evidence_id,又在 preloaded bundles 里找到同 id 且有 final_v1 的那条。
  const matchedBundle = matchAnalysisBundle(video, bundles);
  const analysisBundle = matchedBundle && recordOr(matchedBundle).finalEntry ? matchedBundle : null;

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
      // E1:播放器下方内联该条逐视频深析(只读 final_v1 缓存);命中渲染 AnalysisCard,未命中给空态。
      e("div", { className: "max-h-[42vh] overflow-y-auto border-t border-white/[0.08] px-4 py-3" },
        e("div", { className: "mb-2 flex items-center gap-1.5" },
          e(Sparkles, { size: 11, className: "text-cyan-300" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "该视频逐条深析"),
          e("span", { className: "text-[8.5px] text-slate-600" }, "final_v1 · 只读缓存")
        ),
        analysisBundle
          ? e(AnalysisCard, { bundle: analysisBundle })
          : e("div", { className: "rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-500" },
              !apiToken ? "登录后读取该视频的深析缓存。" : "该视频暂无深析"
            )
      ),
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

// item3:视频 final_v1 的完整层级此前被埋——layer1_summary(内容/时间线/出现度)、
// scores(6 维带 evidence)、recommendations 增量字段、risk.final_verdict 默认都不渲染。
// 折叠在「完整分析」里,默认收起保持卡片简洁,展开看全量。纯展示,不杜撰(缺字段即不渲染)。
function LlmDeepFullBreakdown({ dimensions }: any) {
  const [open, setOpen] = React.useState(false);
  const layer1 = recordOr(dimensions.layer1_summary);
  const scores = recordOr(dimensions.scores);
  const recommendations = recordOr(dimensions.recommendations);
  const risk = recordOr(dimensions.risk);

  const SCORE_LABELS: Record<string, string> = {
    content_quality_score: "内容质量",
    product_proof_score: "产品实证",
    channel_value_score: "渠道价值",
    marketing_value_score: "营销价值",
    viewer_heart_score: "观众好感",
    asset_reuse_score: "素材复用",
  };
  const scoreRows = Object.keys(SCORE_LABELS)
    .map((key) => ({ key, label: SCORE_LABELS[key], cell: recordOr(scores[key]) }))
    .filter((row) => row.cell && (row.cell.score != null || row.cell.evidence));

  const sceneTimeline = asArray(layer1.scene_timeline)
    .map((s: any) => recordOr(s))
    .filter((s: any) => s.what || s.timestamp)
    .slice(0, 8);

  const contentSummary = compactText(layer1.content_summary, 600);
  const presenceRows = [
    { label: "品牌曝光(Viltrox)", value: layer1.brand_exposure },
    { label: "产品出现", value: layer1.product_presence },
    { label: "竞品出现", value: layer1.competitor_presence },
    { label: "制作观察", value: layer1.production_observations },
  ].map((row) => ({ ...row, text: compactText(row.value, 320) })).filter((row) => row.text);

  const extraRecs = [
    { label: "预算动作", value: recommendations.budget_action },
    { label: "须向 KOL 索取", value: recommendations.must_request_from_kol },
    { label: "下次 brief 调整", value: recommendations.next_brief_adjustments },
  ].map((row) => ({ ...row, texts: flexibleTextList(row.value, 3) })).filter((row) => row.texts.length);

  const finalVerdict = compactText(risk.final_verdict, 600);

  const hasContent = Boolean(contentSummary) || sceneTimeline.length > 0 || presenceRows.length > 0
    || scoreRows.length > 0 || extraRecs.length > 0 || Boolean(finalVerdict);
  if (!hasContent) return null;

  return e("div", { className: "mt-2 border-t border-white/[0.05] pt-1.5" },
    e("button", {
      type: "button",
      onClick: () => setOpen((v: boolean) => !v),
      className: "flex w-full items-center justify-between text-[9.5px] text-fuchsia-200/80 hover:text-fuchsia-100",
    },
      "完整分析 · 内容概览 / 6 维评分 / 完整建议",
      e(ChevronsUpDownIcon, { expanded: open })
    ),
    open && e("div", { className: "mt-2 space-y-2.5" },
      contentSummary && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "内容概览"),
        e("div", { className: "text-[10px] leading-relaxed text-slate-300" }, contentSummary)
      ),
      sceneTimeline.length > 0 && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "场景时间线"),
        e("div", { className: "space-y-1" },
          sceneTimeline.map((s: any, i: number) => e("div", { key: i, className: "rounded border border-white/[0.04] bg-black/15 px-2 py-1" },
            e("div", { className: "flex items-baseline gap-1.5" },
              s.timestamp && e("span", { className: "shrink-0 text-[9px] tabular-nums text-cyan-300/80" }, String(s.timestamp)),
              e("span", { className: "text-[10px] text-slate-300" }, compactText(s.what, 160))
            ),
            s.why_it_matters && e("div", { className: "mt-0.5 text-[9px] text-slate-500" }, "→ " + compactText(s.why_it_matters, 140))
          ))
        )
      ),
      presenceRows.length > 0 && e("div", { className: "grid grid-cols-1 gap-1" },
        presenceRows.map((row) => e("div", { key: row.label, className: "rounded border border-white/[0.04] bg-black/15 px-2 py-1" },
          e("div", { className: "text-[9px] text-slate-500" }, row.label),
          e("div", { className: "text-[10px] leading-relaxed text-slate-300" }, row.text)
        ))
      ),
      scoreRows.length > 0 && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "6 维评分(独立信号,非 viltrox_fit_score)"),
        e("div", { className: "space-y-1" },
          scoreRows.map((row) => e("div", { key: row.key, className: "rounded border border-white/[0.04] bg-black/15 px-2 py-1" },
            e("div", { className: "flex items-center justify-between gap-2" },
              e("span", { className: "text-[10px] text-slate-300" }, row.label),
              e("span", { className: "text-[11px] font-semibold tabular-nums text-white" }, scoreText(row.cell.score))
            ),
            row.cell.evidence && e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-slate-500" }, compactText(row.cell.evidence, 240))
          ))
        )
      ),
      extraRecs.length > 0 && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "更多建议"),
        e("div", { className: "space-y-1" },
          extraRecs.map((row) => e("div", { key: row.label, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
            e("div", { className: "mb-0.5 text-[9px] text-fuchsia-200" }, row.label),
            row.texts.map((text: any, i: number) => e("div", { key: i, className: "text-[10px] leading-relaxed text-slate-300" }, text))
          ))
        )
      ),
      finalVerdict && e("div", { className: "rounded border border-amber-300/15 bg-amber-300/[0.04] px-2 py-1.5" },
        e("div", { className: "mb-0.5 text-[9px] text-amber-200" }, "最终裁决"),
        e("div", { className: "text-[10px] leading-relaxed text-slate-300" }, finalVerdict)
      )
    )
  );
}

// item4:账号档案完整展示——读 /account-dossier(本地聚合,零 LLM/provider/写库)。
// 展示覆盖度(视频/已分析/QA/抓取/营销分)、缺口、判断(fit+建议+风险)、最近事件。默认折叠。
function AccountDossierPanel({ apiToken, kolPoolId }: any) {
  const [dossier, setDossier] = React.useState<any>(null);
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    setDossier(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void getKolPoolAccountDossier(apiToken, kolPoolId)
      .then((payload: any) => { if (!cancelled) setDossier(payload && payload.status === "ready" ? payload : null); })
      .catch(() => { if (!cancelled) setDossier(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!dossier) return null;
  const coverage = recordOr(dossier.coverage);
  const judgment = recordOr(dossier.judgment);
  const gaps = asArray(dossier.gaps).map((g: any) => compactText(typeof g === "string" ? g : (recordOr(g).label || recordOr(g).reason || ""), 60)).filter(Boolean);
  const events = asArray(dossier.events);
  const platformCounts = recordOr(coverage.platform_counts);

  const statCells = [
    { label: "视频证据", value: numberOr(coverage.video_evidence_count) },
    { label: "已深析", value: numberOr(coverage.analyzed_final_v1_count) },
    { label: "关键帧QA", value: numberOr(coverage.qa_count) },
    { label: "抓取次数", value: numberOr(coverage.crawl_run_count) },
  ].map((c) => ({ ...c, value: c.value == null ? 0 : c.value }));
  const mvAvg = numberOr(coverage.marketing_value_score_avg);
  const mvMax = numberOr(coverage.marketing_value_score_max);
  const cqAvg = numberOr(coverage.content_quality_score_avg);
  const cqMax = numberOr(coverage.content_quality_score_max);
  const oneLineVerdict = compactText(judgment.one_line_verdict || "", 160);
  const fit = numberOr(judgment.primary_llm_v6_fit);
  const judgmentRecs = flexibleTextList(judgment.recommendations, 3);
  const judgmentRisk = flexibleTextList(recordOr(judgment.risk).risk_flags, 3);
  const recentEvents = events.slice(0, 5).map((ev: any) => recordOr(ev));

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("button", {
      type: "button",
      onClick: () => setOpen((v: boolean) => !v),
      className: "flex w-full items-center justify-between",
    },
      e("div", { className: "flex items-center gap-1.5" },
        e(UserCircle2Icon, null),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "账号档案"),
        fit != null && e("span", { className: "rounded bg-fuchsia-500/12 px-1.5 py-0.5 text-[9px] text-fuchsia-200" }, "LLM fit " + scoreText(fit))
      ),
      e(ChevronsUpDownIcon, { expanded: open })
    ),
    oneLineVerdict && e("div", { className: "mt-2 rounded-md border border-cyan-300/15 bg-cyan-400/[0.06] px-2.5 py-1.5 text-[11px] font-medium leading-relaxed text-cyan-50" }, "📊 " + oneLineVerdict),
    e("div", { className: "mt-2 grid grid-cols-4 gap-1.5" },
      statCells.map((c) => e("div", { key: c.label, className: "rounded border border-white/[0.04] bg-black/15 px-2 py-1.5 text-center" },
        e("div", { className: "text-[14px] font-bold tabular-nums text-white" }, String(c.value)),
        e("div", { className: "text-[8.5px] text-slate-500" }, c.label)
      ))
    ),
    (cqAvg != null || mvAvg != null) && e("div", { className: "mt-1.5 flex flex-wrap items-center gap-3 text-[9.5px] text-slate-400" },
      cqAvg != null && e("span", null, "内容质量 ", e("span", { className: "tabular-nums text-cyan-200 font-semibold" }, scoreText(cqAvg)), cqMax != null && e("span", { className: "text-slate-600" }, " /峰" + scoreText(cqMax))),
      mvAvg != null && e("span", null, "投放价值 ", e("span", { className: "tabular-nums text-emerald-200 font-semibold" }, scoreText(mvAvg)), mvMax != null && e("span", { className: "text-slate-600" }, " /峰" + scoreText(mvMax))),
      Object.keys(platformCounts).length > 0 && e("span", { className: "text-slate-600" }, "· " + Object.entries(platformCounts).map(([k, v]) => `${k}:${v}`).join(" "))
    ),
    gaps.length > 0 && e("div", { className: "mt-1.5 flex flex-wrap gap-1" },
      gaps.slice(0, 6).map((g: string, i: number) => e("span", { key: i, className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[8.5px] text-amber-200" }, "缺口: " + g))
    ),
    open && e("div", { className: "mt-2 space-y-2" },
      judgmentRecs.length > 0 && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "账号级建议"),
        judgmentRecs.map((t: string, i: number) => e("div", { key: i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1 text-[10px] leading-relaxed text-slate-300" }, t))
      ),
      judgmentRisk.length > 0 && e("div", { className: "rounded border border-amber-300/15 bg-amber-300/[0.04] px-2 py-1.5" },
        e("div", { className: "mb-1 text-[9px] text-amber-200" }, "风险旗标"),
        judgmentRisk.map((t: string, i: number) => e("div", { key: i, className: "text-[10px] leading-relaxed text-slate-300" }, t))
      ),
      recentEvents.length > 0 && e("div", null,
        e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-slate-500" }, "最近事件"),
        e("div", { className: "space-y-1" },
          recentEvents.map((ev: any, i: number) => e("div", { key: i, className: "flex items-center justify-between gap-2 rounded border border-white/[0.04] bg-black/15 px-2 py-1" },
            e("span", { className: "truncate text-[10px] text-slate-300" }, compactText(recordOr(ev.summary).title || ev.event_type || "—", 40)),
            e("span", { className: "shrink-0 text-[9px] text-slate-500" }, String(ev.status || "") + " · " + String(ev.occurred_at || "").slice(0, 10))
          ))
        )
      )
    )
  );
}

function UserCircle2Icon() {
  return e(Sparkles, { size: 11, className: "text-cyan-300" });
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
      e(LlmDeepFullBreakdown, { dimensions }),
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

// D2:CopyValueButton / GoaffproLinkCard / GoaffproLinkSection 已抽出至共享文件 ../../shared/GoaffproLinkSection(供 MY KOL 详情复用),渲染调用见下方。

export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact, staff = [] }: any) {
  // P-GROUP-7 共享 KOL 池:把这条 My KOL(item.id = kol_pool_id)显式共享给成员(只读授予)。
  const [shareOpen, setShareOpen] = React.useState(false);
  const [dimensions11, setDimensions11] = React.useState<any>(null);
  const [llmDeepAnalysis, setLlmDeepAnalysis] = React.useState<any>(null);
  const [preloadedVideoAnalysisBundles, setPreloadedVideoAnalysisBundles] = React.useState<any>(undefined);
  const [videoAnalysisSummary, setVideoAnalysisSummary] = React.useState<any>(null);
  const [videoEnqueueState, setVideoEnqueueState] = React.useState<any>({ status: "idle", message: "" });
  // 全视频跑:该 KOL 全部视频证据各入队一条 final_v1,发完综合评估。独立于上面的单代表作入队。
  const [allVideosState, setAllVideosState] = React.useState<any>({ status: "idle", message: "" });
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
  const allVideosBusy = allVideosState.status === "loading";
  const handleEnqueueAllVideos = () => {
    if (!apiToken || !item.id || allVideosBusy) return;
    setAllVideosState({ status: "loading", message: "正在把该 KOL 的全部视频证据加入深析队列…" });
    void enqueueAllKolVideos(apiToken, item.id)
      .then((payload: any) => {
        const status = String(payload?.status || "");
        if (status === "no_evidence") {
          setAllVideosState({ status: "no_evidence", message: payload?.reason || "该 KOL 暂无视频证据,需先发现/抓取视频再全视频分析" });
          return;
        }
        const queued = Number(payload?.queued || 0);
        const skipped = Number(payload?.skipped || 0);
        const total = Number(payload?.evidence_total || payload?.requested || 0);
        setAllVideosState({ status: "done", message: `全视频跑:共 ${total} 条,入队 ${queued},跳过 ${skipped}(已分析/在队);进度见左侧看板` });
      })
      .catch((error: any) => {
        setAllVideosState({ status: "error", message: error?.message ? String(error.message) : "全视频入队失败" });
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

      // ── item4 账号档案(本地聚合,零 LLM):覆盖度/缺口/账号级判断/最近事件 ──
      e(AccountDossierPanel, { apiToken, kolPoolId: item?.id }),

      // ── 长期记忆(W3)· 显式独立于 V6 Fit · 不影响排序 ──
      // 红线:本区块纯渲染聚合记忆,绝不渲染任何 viltrox/v6_fit 数值。
      e(KOLDrawerMemorySection, { kolMemory }),

      // ── 联系方式 & 代表视频 ──
      e(KOLDrawerContactAndVideos, { item, representativeVideos, onOpenVideo: setActiveRepresentativeVideo }),
      e(KOLVideoAnalysisPanel, { apiToken, videos: videoAnalysisVideos, preloadedBundles: preloadedVideoAnalysisBundles, summary: videoAnalysisSummary }),
      // ── 全视频跑:该 KOL 全部视频证据各入队一条 final_v1 → 发完综合评估 ──
      apiToken && item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
        e("button", {
          type: "button",
          disabled: allVideosBusy,
          onClick: handleEnqueueAllVideos,
          className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-200 transition-colors hover:bg-cyan-400/[0.12] disabled:opacity-50",
        },
          e(Sparkles, { size: 12 }),
          allVideosBusy ? "深度分析入队中…" : "KOL深度分析理解(最近20条)"
        ),
        allVideosState.message && e("div", {
          className: "mt-1.5 text-[9.5px] leading-relaxed " + (allVideosState.status === "error" ? "text-rose-300" : allVideosState.status === "no_evidence" ? "text-amber-300" : "text-slate-500")
        }, allVideosState.message)
      ),
      // ── P-GROUP-7 共享给成员:把这条 My KOL 显式共享给某成员(只读授予,落 vkpi_kol_pool_members)──
      apiToken && item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
        e("button", {
          type: "button",
          onClick: () => setShareOpen(true),
          className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-purple-400/25 bg-purple-400/[0.06] px-3 py-2 text-[11px] font-medium text-purple-200 transition-colors hover:bg-purple-400/[0.12]",
        },
          e(Share2, { size: 12 }),
          "共享给成员"
        )
      ),
      // ── D2 生成追踪链(GOAFFPRO):一键给该 KOL 建 affiliate + 追踪链 + 优惠码(KOL 零注册)──
      e(GoaffproLinkSection, { apiToken, kolPoolId: item?.id }),
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
      // E1:把 preloaded video_analysis bundles + token 传进播放器,内联该条逐视频深析。
      bundles: Array.isArray(preloadedVideoAnalysisBundles) ? preloadedVideoAnalysisBundles : null,
      apiToken,
    }),
    shareOpen && e(ShareKolModal, {
      kolPoolId: String(item?.id ?? ""),
      kolName: item?.name || item?.handle || String(item?.id ?? ""),
      staff,
      apiToken,
      onClose: () => setShareOpen(false),
    })
  );
}
