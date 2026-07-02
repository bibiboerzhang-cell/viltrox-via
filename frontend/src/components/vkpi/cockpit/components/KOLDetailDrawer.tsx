// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Share2, Sparkles } from "lucide-react";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";
import { ShareKolModal } from "../../shared/ShareKolModal";
import { enqueueAllKolVideos, enqueueVideoAnalysis, getKolPoolContentFit, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis, promoteKolPoolToMain, refreshAudienceStats } from "../../../../services/vkpi/kolPool-api";
import { getKolMemory } from "../../../../services/vkpi/kolMemory-api";
import { runSkill, type SkillRunResult } from "../../../../services/vkpi/skills-api";
import { GoaffproLinkSection } from "../../shared/GoaffproLinkSection";
import {
  asArray,
  detailBundleAnalysisItems,
  detailBundleAnalysisSummary,
  dimensions11RadarDims,
  evidenceIdOf,
  recordOr,
  videoAnalysisSources,
} from "./KOLDetailDrawer.helpers";
import {
  AccountDossierPanel,
  CooperationPanel,
  LlmDeepAnalysisPanel,
  RepresentativeVideoPlayerModal,
} from "./KOLDetailDrawer.Panels";
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

// 行为不变重构:CopyEmailButton / KOLDetailAvatar / RepresentativeVideoCard 已搬到 ./KOLDetailDrawer.Panels,
// 这里 re-export 以保留 KOLDetailDrawerSections.tsx 既有 `import { ... } from "./KOLDetailDrawer"` 的对外契约。
export { CopyEmailButton, KOLDetailAvatar, RepresentativeVideoCard } from "./KOLDetailDrawer.Panels";

const e = React.createElement;

// D2:CopyValueButton / GoaffproLinkCard / GoaffproLinkSection 已抽出至共享文件 ../../shared/GoaffproLinkSection(供 MY KOL 详情复用),渲染调用见下方。

export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact, staff = [], onReloadDetail }: any) {
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
  // N2 Skill 触发:跑 brief_generate skill 为该 KOL 生成合作 brief 草案(默认走规则模板,不烧 LLM)。
  const [briefResult, setBriefResult] = React.useState<SkillRunResult | null>(null);
  const [briefBusy, setBriefBusy] = React.useState(false);
  const [briefError, setBriefError] = React.useState("");
  // 受众画像 ensemble_v1(P0):Audience Stats·估算 BETA 面板的刷新态(state 在此,展示组件保持纯 props)。
  const [audienceState, setAudienceState] = React.useState<any>({ status: "idle", message: "" });
  const handleRefreshAudience = React.useCallback(() => {
    if (!apiToken || !item?.id) return;
    setAudienceState({ status: "loading", message: "" });
    void refreshAudienceStats(apiToken, item.id)
      .then((payload: any) => {
        const status = String(payload?.status || "");
        if (status === "ok") {
          setAudienceState({ status: "done", message: `已更新:样本 ${payload?.sample_size ?? "—"} 评论者 · 置信 ${payload?.confidence ?? "—"}` });
          // 完成后重拉 detail_bundle,让面板吃到新 audience_estimated。
          if (typeof onReloadDetail === "function") void onReloadDetail();
        } else if (status === "pending_comments") {
          setAudienceState({
            status: "pending",
            message: payload?.enqueued ? "评论不足,已入队抓评论 — 可稍后再刷新" : String(payload?.reason || "评论不足,稍后再试"),
          });
        } else if (status === "no_posts" || status === "no_commenters") {
          // 数据前置条件缺失(无帖子记录/帖子无评论)—— 中性提示引导下一步,不按报错渲染。
          setAudienceState({
            status: "pending",
            message: String(payload?.reason || (status === "no_posts" ? "暂无该 KOL 的帖子记录,先跑一次账号/视频分析" : "已收录帖子暂无可用评论")),
          });
        } else {
          setAudienceState({ status: "error", message: String(payload?.reason || payload?.status || "刷新失败") });
        }
      })
      .catch((error: any) => setAudienceState({ status: "error", message: error?.message ? String(error.message) : "刷新失败" }));
  }, [apiToken, item?.id, onReloadDetail]);
  // v2 证据下钻:面板内可展开块(intent/brands/fans)单开手风琴;state 在父层,子组件仍零 state。
  const [audienceExpand, setAudienceExpand] = React.useState<string | null>(null);
  const handleToggleAudienceBlock = React.useCallback((key: string) => {
    setAudienceExpand((prev) => (prev === key ? null : key));
  }, []);
  // #1 入主表 promote:把候选写进 vkpi 主表(接已存在 /kol-pool/{id}/promote)。
  const [promoteMsg, setPromoteMsg] = React.useState<{ ok: boolean; text: string } | null>(null);
  const onPromote = React.useCallback(async (it: any) => {
    if (!apiToken || !it?.id) return;
    setPromoteMsg(null);
    try {
      await promoteKolPoolToMain(apiToken, Number(it.id));
      setPromoteMsg({ ok: true, text: "已入主表" });
    } catch (err: any) {
      setPromoteMsg({ ok: false, text: String(err && err.message ? err.message : err) });
    }
  }, [apiToken]);
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
    // 换 KOL 时清掉上一条的 brief skill 结果,避免串号。
    setBriefResult(null);
    setBriefError("");
    setBriefBusy(false);
    // 换 KOL 时受众刷新态/证据展开态归零,避免上一条的 loading/报错/展开串号。
    setAudienceState({ status: "idle", message: "" });
    setAudienceExpand(null);
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

  // N2 跑 Skill:brief_generate 需要 kol_pool_id + product;product 取该 KOL 首个推荐产品线,缺则回落通用产品名。
  const handleRunBriefSkill = () => {
    if (!apiToken || !item?.id || briefBusy) return;
    const productLines = asArray(item.recommended_product_lines)
      .map((x: any) => (typeof x === "string" ? x : String(x?.name ?? x?.label ?? "")).trim())
      .filter(Boolean);
    const product = productLines[0] || "Viltrox 镜头";
    setBriefBusy(true);
    setBriefError("");
    setBriefResult(null);
    void runSkill(apiToken, "brief_generate", { kol_pool_id: Number(item.id), product })
      .then((res) => {
        setBriefResult(res && typeof res === "object" ? res : null);
        const out = (res && typeof res.output === "object" ? res.output : null) as Record<string, unknown> | null;
        if (out && out.ok === false) {
          setBriefError(String(out.reason || "skill 未产出结果"));
        }
      })
      .catch((err: any) => setBriefError(err?.message ? String(err.message) : "跑 Skill 失败,请稍后重试。"))
      .finally(() => setBriefBusy(false));
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
      e(CooperationPanel, { apiToken, kolPoolId: item?.id }),

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
      // ── N2 跑 Skill:brief_generate → 合作 brief 草案(默认走规则模板,不烧 LLM;红线零触 fit)──
      apiToken && item?.id && e(KOLDrawerBriefSkill, {
        result: briefResult, busy: briefBusy, error: briefError, onRun: handleRunBriefSkill,
      }),
      // 地基B:内容契合深析(content_fit_v1)——基于视频画面/故事 + 评论的适配判断(胜过粉丝数)。
      e(KOLDrawerContentFit, { apiToken, item, contentFit, contentFitBusy, contentFitError, onAnalyze: handleContentFitAnalyze }),
      e(KOLDrawerDevices, { item, devices }),

      // ── Audience Stats·估算 BETA + Geo distribution(组件内部自判空;无数据但可刷新时也渲染)──
      e(KOLDrawerGeoDistribution, {
        item, geoDistribution, apiToken, audienceState,
        onRefreshAudience: handleRefreshAudience,
        audienceExpand, onToggleAudienceBlock: handleToggleAudienceBlock,
      }),

      // ── V6 Fit Breakdown ──
      e(KOLDrawerV6Breakdown, { item, v6Breakdown }),

      // ── Trend hits ──
      trendHits.length > 0 && e(KOLDrawerTrendHits, { trendHits }),

      // ── Viltrox 适配判断 / 推荐产品线 / 风险点 / 品牌合作历史 ──
      e(KOLDrawerTextSections, { item, recommendedProductLines, potentialConcerns, brandCollaborations, competitorCollabs }),
    ),
    
    // ─── Footer actions ───
    e(KOLDrawerFooter, {
      item, inMyList, onToggleMyList, onContact, onPromote, promoteMsg,
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

// ── N2 跑 Skill 区块:brief_generate 触发按钮 + loading/错误/结果三态渲染 ──
function KOLDrawerBriefSkill({ result, busy, error, onRun }: {
  result: SkillRunResult | null;
  busy: boolean;
  error: string;
  onRun: () => void;
}) {
  const output = (result && typeof result.output === "object" ? result.output : null) as Record<string, unknown> | null;
  const brief = (output && typeof output.brief === "object" ? output.brief : null) as Record<string, unknown> | null;
  const list = (value: unknown): string[] => (Array.isArray(value) ? value.map((x) => String(x)).filter(Boolean) : []);
  const hook = brief && typeof brief.hook === "string" ? brief.hook : "";
  const talkingPoints = brief ? list(brief.talking_points) : [];
  const dos = brief ? list(brief.do) : [];
  const donts = brief ? list(brief.dont) : [];
  const deliverables = brief ? list(brief.deliverables) : [];
  const okFalse = output ? output.ok === false : false;
  const showResult = Boolean(result) && Boolean(brief) && !okFalse;

  const renderListBlock = (label: string, items: string[], color: string) =>
    items.length > 0 && e("div", { key: label },
      e("div", { className: "text-[9px] mb-1", style: { color } }, label),
      e("ul", { className: "space-y-0.5" },
        items.map((it, i) => e("li", { key: i, className: "text-[10px] text-slate-300 leading-snug" }, "· " + it)),
      ),
    );

  return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center justify-between gap-2 mb-1.5" },
      e("div", { className: "text-[11px] font-semibold text-white" }, "合作 Brief 草案 · Skill"),
      result?.skill_run_id != null && e("span", {
        className: "text-[9px] px-1.5 py-0.5 rounded bg-white/[0.06] text-slate-400",
      }, "run #" + result.skill_run_id),
    ),
    e("button", {
      type: "button",
      disabled: busy,
      onClick: onRun,
      className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-emerald-400/25 bg-emerald-400/[0.06] px-3 py-2 text-[11px] font-medium text-emerald-200 transition-colors hover:bg-emerald-400/[0.12] disabled:opacity-50",
    },
      e(Sparkles, { size: 12 }),
      busy ? "跑 Skill 中…" : (showResult ? "重新生成 Brief" : "跑 Skill·生成合作 Brief"),
    ),
    error && e("div", { className: "mt-1.5 text-[9.5px] leading-relaxed text-rose-300" }, error),
    showResult && e("div", { className: "mt-2 rounded-md border border-white/[0.05] bg-black/20 p-2.5 space-y-2" },
      hook && e("div", null,
        e("div", { className: "text-[9px] text-emerald-300 mb-0.5" }, "开场钩子"),
        e("div", { className: "text-[10.5px] text-slate-200 leading-relaxed" }, hook),
      ),
      renderListBlock("内容要点", talkingPoints, "#06b6d4"),
      renderListBlock("建议做", dos, "#10b981"),
      renderListBlock("避免", donts, "#fb7185"),
      renderListBlock("交付物", deliverables, "#a855f7"),
      output && typeof output.model_used === "string" && e("div", {
        className: "text-[9px] text-slate-500 pt-1",
      }, "模型 " + output.model_used + " · 草案仅供人审后编辑"),
    ),
  );
}
