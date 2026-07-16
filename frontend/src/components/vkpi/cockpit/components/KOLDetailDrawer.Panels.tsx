// 纯重构:从 KOLDetailDrawer.tsx 行为不变搬出的独立展示型子组件(各自内聚、自带内部 hooks)。
// 容器组件 KOLDetailDrawer 本体仍留在 KOLDetailDrawer.tsx;此处仅承接面板/卡片/弹窗。
// 红线:零触 viltrox_fit_score 写,纯渲染。

import React from "react";
import { m } from "framer-motion";
import { AlertTriangle, Check, Link2, Shield, Sparkles, Video } from "lucide-react";
import { KPAvatar } from "./KPAvatar";
import { AnalysisCard } from "./KOLVideoAnalysisPanel";
import { proxiedImageUrl, proxiedVideoUrl } from "../../shared/mediaProxy";
import { getKolCooperation, getKolPoolAccountDossier, recordKolCooperation } from "../../../../services/vkpi/kolPool-api";
import { SectionFold } from "./SectionFold";
import { KOLDrawerBrandExposure } from "./KOLDrawerBrandExposure";
import { CommerceSignalsPanel } from "./CommerceSignalsPanel";
import {
  asArray,
  compactText,
  detailAvatarUrl,
  evidenceIdOf,
  fixedOrDash,
  flexibleTextList,
  matchAnalysisBundle,
  mediaKindBadge,
  normalizedVideoPlatform,
  numberOr,
  recordOr,
  scoreText,
  videoString,
  youtubeEmbedUrl,
  youtubeIdForVideo,
} from "./KOLDetailDrawer.helpers";

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

// 【C5】compact:一行紧凑小图模式(高约 56px 横排,hover 放大 + title 提示),省抽屉纵向空间;
// 点击行为与大卡完全一致(onOpen 同一回调)。默认 false 保持原大卡渲染,别处调用零影响。
export function RepresentativeVideoCard({ video, index, onOpen, compact = false }: any) {
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
  const platformLabel = platform === "instagram" ? "IG" : platform === "tiktok" ? "TT" : platform === "youtube" ? "YT" : "MEDIA";
  // 【K4】媒体种类小徽章(图/组图/视频):读 evidence 真实字段 media_kind/evidence_type,缺失不显示。
  const kindBadge = mediaKindBadge(video);

  const handleClick = () => {
    if (!canOpen) return;
    onOpen?.(video);
  };

  if (compact) {
    return e("button", {
      type: "button",
      className: [
        "group relative h-[56px] w-[100px] shrink-0 overflow-hidden rounded-md border border-white/[0.06] bg-white/[0.02] text-left transition-colors",
        canOpen ? "cursor-pointer hover:border-cyan-300/30 focus:outline-none focus:ring-1 focus:ring-cyan-300/30" : "cursor-default",
      ].join(" "),
      onClick: handleClick,
      // hover 提示:小图没地方放标题/播放数,全塞 title tooltip。
      title: title + " · " + views + " 播放" + (canOpen ? " · 点击播放" : ""),
    },
      e("div", { className: "absolute inset-0 flex items-center justify-center bg-gradient-to-br from-slate-700 to-slate-900" },
        showThumbnail
          ? e("img", {
              src: thumbnail,
              alt: title,
              className: "h-full w-full object-cover transition-transform duration-150 group-hover:scale-110",
              loading: "lazy",
              referrerPolicy: "no-referrer",
              onError: () => setThumbnailFailed(true),
            })
          : e(Video, { size: 14, className: "text-slate-500" })
      ),
      e("span", {
        className: "absolute left-0.5 top-0.5 rounded px-1 py-px text-[7px] font-medium uppercase tracking-wide text-white/85",
        style: { background: "rgba(0,0,0,0.62)" }
      }, platformLabel),
      // 【K4】媒体种类徽章(紧凑模式:平台标右侧)
      kindBadge && e("span", {
        className: "absolute left-0.5 top-4 rounded px-1 py-px text-[7px] font-medium text-cyan-100/90",
        style: { background: "rgba(8,51,68,0.78)" },
        title: kindBadge.title,
      }, kindBadge.label),
      e("span", {
        className: "absolute bottom-0.5 right-0.5 rounded px-1 text-[7.5px] tabular-nums text-white",
        style: { background: "rgba(0,0,0,0.7)" }
      }, duration)
    );
  }

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
      // 【K4】媒体种类徽章(大卡:平台标下方),字段缺失时 kindBadge=null 不渲染
      kindBadge && e("span", {
        className: "absolute left-1 top-6 rounded px-1 py-0.5 text-[7.5px] font-medium text-cyan-100/90",
        style: { background: "rgba(8,51,68,0.78)" },
        title: kindBadge.title,
      }, kindBadge.label),
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
export function RepresentativeVideoPlayerModal({ video, onClose, bundles = null, apiToken = "" }: any) {
  const title = videoString(video, ["title", "video_title"], "代表作");
  const thumbnail = proxiedImageUrl(videoString(video, ["best_thumbnail", "thumbnail_url", "youtube_thumbnail_url"]));
  const cachedVideoUrl = proxiedVideoUrl(videoString(video, ["cached_video_url"]));
  const youtubeVideoId = youtubeIdForVideo(video);
  const watchUrl = videoString(video, ["watch_url", "url", "content_url"]);
  // watch_url 可以是有效缓存播放地址；“打开原帖”必须优先保留真实 content_url，
  // 否则缓存失效后按钮仍会指回同一条坏路由。
  const originalPostUrl = videoString(video, ["content_url", "url"]) || watchUrl;
  const platform = normalizedVideoPlatform(video);
  const embedSrc = youtubeEmbedUrl(youtubeVideoId);
  const [cachedVideoFailed, setCachedVideoFailed] = React.useState(false);
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

  React.useEffect(() => {
    setCachedVideoFailed(false);
  }, [cachedVideoUrl, originalPostUrl]);

  const stage = cachedVideoUrl && !cachedVideoFailed
    ? e("video", {
          src: cachedVideoUrl,
          poster: thumbnail || undefined,
          className: "h-full w-full rounded-lg bg-black object-contain",
          controls: true,
          autoPlay: true,
          playsInline: true,
          onError: () => setCachedVideoFailed(true),
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
          originalPostUrl && e("a", {
            href: originalPostUrl,
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
              cachedVideoFailed
                ? "缓存视频加载失败"
                : platform === "instagram" || platform === "tiktok" ? "当前未命中 R2 视频缓存" : "当前没有可内嵌播放的视频缓存"
            ),
            e("div", { className: "mt-1 text-xs text-slate-500" },
              cachedVideoFailed
                ? "已停止使用失效缓存；可以打开原帖查看"
                : platform === "instagram" || platform === "tiktok" ? "不会使用 YouTube 播放器；可以打开原帖查看" : "可以打开原帖查看"
            )
          ),
          originalPostUrl && e("a", {
            href: originalPostUrl,
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
    e(m.div, {
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
      originalPostUrl && e("footer", { className: "flex items-center justify-between gap-3 border-t border-white/[0.08] px-4 py-2" },
        e("span", { className: "truncate text-[10px] text-slate-500" }, originalPostUrl),
        e("a", {
          href: originalPostUrl,
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
export function LlmDeepFullBreakdown({ dimensions }: any) {
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

// P1-1 合作动作(平台为基准):续约/加大投入/评估/退出 → 写 vkpi_kol_cooperation_events,
// 当前状态=最新事件;按真 kol_pool_id 落库。零触 viltrox_fit_score。
export function CooperationPanel({ apiToken, kolPoolId }: any) {
  const [coop, setCoop] = React.useState<any>(null);
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState("");
  const [err, setErr] = React.useState("");

  const load = React.useCallback(() => {
    if (!apiToken || !kolPoolId) return;
    void getKolCooperation(apiToken, kolPoolId)
      .then((payload: any) => setCoop(payload || null))
      .catch(() => setCoop(null));
  }, [apiToken, kolPoolId]);
  React.useEffect(() => { setCoop(null); load(); }, [load]);

  if (!apiToken || !kolPoolId) return null;
  const status = coop?.status || "";
  const statusLabel = coop?.status_label || "未设定";
  const events = Array.isArray(coop?.events) ? coop.events : [];
  const statusColor = status === "exited" ? "#f87171" : status === "scaling" ? "#34d399"
    : status === "active" ? "#22d3ee" : status === "evaluating" ? "#fbbf24" : "#94a3b8";
  const fmtTime = (raw: any) => {
    if (!raw) return "";
    const d = new Date(String(raw));
    return Number.isNaN(d.getTime()) ? "" : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };
  const act = async (action: string) => {
    if (busy) return;
    setBusy(action); setErr("");
    try {
      const res: any = await recordKolCooperation(apiToken, kolPoolId, action);
      if (res && res.status !== undefined) setCoop(res); else load();
    } catch (ex: any) {
      setErr(String(ex && ex.message ? ex.message : ex));
    } finally { setBusy(""); }
  };
  const ACTIONS = [
    { k: "renew", l: "续约", c: "#22d3ee" },
    { k: "scale_up", l: "加大投入", c: "#34d399" },
    { k: "evaluate", l: "评估", c: "#fbbf24" },
    { k: "exit", l: "退出合作", c: "#f87171" },
  ];

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("button", {
      type: "button",
      onClick: () => setOpen((v: boolean) => !v),
      className: "flex w-full items-center justify-between",
    },
      e("div", { className: "flex items-center gap-1.5" },
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "合作动作"),
        e("span", { className: "rounded px-1.5 py-0.5 text-[9px]", style: { background: statusColor + "1f", color: statusColor } }, statusLabel)
      ),
      e(ChevronsUpDownIcon, { expanded: open })
    ),
    e("div", { className: "mt-2 grid grid-cols-4 gap-1.5" },
      ACTIONS.map((a) => e("button", {
        key: a.k,
        type: "button",
        disabled: !!busy,
        onClick: () => act(a.k),
        className: "rounded border px-2 py-1.5 text-[10.5px] font-medium",
        style: { borderColor: a.c + "55", color: a.c, background: busy === a.k ? a.c + "22" : "transparent" },
      }, busy === a.k ? "…" : a.l))
    ),
    err && e("div", { className: "mt-1.5 text-[10px] text-rose-300" }, "⚠ " + err),
    open && (events.length > 0
      ? e("div", { className: "mt-2 space-y-1" },
          events.slice(0, 8).map((ev: any, i: number) => e("div", {
            key: i,
            className: "flex items-center justify-between rounded border border-white/[0.04] bg-black/15 px-2 py-1 text-[10px]",
          },
            e("span", { className: "text-slate-200" }, (ev.action_label || ev.action_type) + (ev.note ? " · " + ev.note : "")),
            e("span", { className: "text-slate-500 tabular-nums" }, fmtTime(ev.created_at))
          ))
        )
      : e("div", { className: "mt-2 text-[10px] text-slate-500" }, "暂无合作记录 · 点上方动作开始"))
  );
}

// item4:账号档案完整展示——读 /account-dossier(本地聚合,零 LLM/provider/写库)。
// 展示覆盖度(视频/已分析/QA/抓取/营销分)、缺口、判断(fit+建议+风险)、最近事件。默认折叠。
export function AccountDossierPanel({ apiToken, kolPoolId }: any) {
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

  // 【#51】品牌露出 · 百家饭指数借本面板接线(深度分析 tab):组件自拉取/自判空,
  // 与 dossier 数据源完全独立 —— dossier 缺失时也要渲染,两条 return 路径都带上。
  // 第2轮 商业信号(制作周期+竞争活跃)与 dossier 数据源独立 —— dossier 缺失时也渲染,两条路径都带。
  if (!dossier) return e(React.Fragment, null,
    e(KOLDrawerBrandExposure, { apiToken, kolPoolId }),
    e(CommerceSignalsPanel, { apiToken, kolPoolId })
  );
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

  // 可收起:整块折叠交给 SectionFold(chevron 由它出);原「展开 ▼」控件仍管内部详情(建议/风险/事件),
  // 保留在 header 内改为 span + stopPropagation(button 不能嵌 button,点它只切详情、不折叠整块)。
  return e(React.Fragment, null,
    e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "dossier",
      header: e(React.Fragment, null,
        e(UserCircle2Icon, null),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "账号档案"),
        fit != null && e("span", { className: "rounded bg-fuchsia-500/12 px-1.5 py-0.5 text-[9px] text-fuchsia-200" }, "LLM fit " + scoreText(fit)),
        e("span", {
          role: "button",
          title: open ? "收起详情(建议/风险/事件)" : "展开详情(建议/风险/事件)",
          onClick: (ev: any) => { ev.stopPropagation(); setOpen((v: boolean) => !v); },
          className: "ml-1",
        }, e(ChevronsUpDownIcon, { expanded: open }))
      ),
    },
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
    ))
    ),
    // 【#51】品牌露出小块紧随账号档案(同属 final_v1 深析产物的账号级读数)。
    e(KOLDrawerBrandExposure, { apiToken, kolPoolId }),
    // 第2轮 商业信号:制作周期(接单→发布 lead time)+ 竞争活跃(近期品牌合作密度)。
    e(CommerceSignalsPanel, { apiToken, kolPoolId })
  );
}

function UserCircle2Icon() {
  return e(Sparkles, { size: 11, className: "text-cyan-300" });
}

export function LlmDeepAnalysisPanel({ payload }: any) {
  // 2026-07-02 诚实空态:此前无数据整块 return null,新发现 KOL 右侧"啥都没有"、
  // 用户不知道该点什么 —— 现在给出引导(深析靠上方「AI深度分析/全部深析」按钮入队生成)。
  if (!payload || payload.status !== "ready" || !payload.primary_result) {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e("div", { className: "flex items-center gap-1.5 mb-1" },
        e(Sparkles, { size: 11, className: "text-slate-500" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "LLM 深度判断")
      ),
      e("div", { className: "text-[10.5px] text-slate-500 leading-relaxed" },
        "该 KOL 还没跑过深度分析 —— 点上方「AI深度分析」(单条代表作)或「全部深析」(全部视频)入队,几分钟后自动出现;进度见左下角任务板。"
      )
    );
  }
  const primary = recordOr(payload.primary_result);
  const resultKind = String(primary.result_kind || (
    String(primary.provider || "").toLowerCase() === "local_extract" ? "local_aggregate" : "unverified_ai"
  ));
  const isLocalAggregate = resultKind === "local_aggregate";
  const isVerifiedLlm = resultKind === "llm";
  const resultTitle = isLocalAggregate
    ? "账号基础聚合（无 LLM）"
    : isVerifiedLlm
      ? "LLM 深度判断"
      : "历史 AI 判断（来源待核验）";
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
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, resultTitle)
      ),
      e("span", { className: "text-[8.5px] text-slate-600" },
        isLocalAggregate ? "local_extract · zero provider calls" : isVerifiedLlm ? "verified provider lineage" : "lineage incomplete")
    ),
    e("div", { className: "rounded-md border border-fuchsia-400/15 bg-fuchsia-400/[0.035] p-2.5" },
      isLocalAggregate && e("div", {
        className: "mb-2 rounded border border-cyan-300/15 bg-cyan-300/[0.04] px-2 py-1 text-[9.5px] leading-relaxed text-cyan-100/80",
      }, "这是本地规则/档案聚合，不是外部大模型结果；真实 LLM 深析仍需 final_v1 与 provider lineage。"),
      e("div", { className: "flex items-start justify-between gap-3" },
        e("div", null,
          e("div", { className: "text-[9px] uppercase tracking-wider text-fuchsia-200/80" },
            isLocalAggregate ? "本地聚合 · 独立于V6 Fit" : "AI判断 · 独立于V6 Fit"),
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
