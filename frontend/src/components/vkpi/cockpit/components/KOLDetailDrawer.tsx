import React from "react";
import { m } from "framer-motion";
import { Sparkles } from "lucide-react";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";
import { ShareKolModal } from "../../shared/ShareKolModal";
import { analyzeKolPoolContentFit, enqueueAllKolVideos, enqueueKolProfileCrawl, enqueueVideoAnalysis, getKolPoolContentFit, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis, getKolVideoAnalysisBatch, getKolVideoAnalysisCache, promoteKolPoolToMain, refreshAudienceStats } from "../../../../services/vkpi/kolPool-api";
import { getKolMemory } from "../../../../services/vkpi/kolMemory-api";
import { KOLDrawerOutreachSection } from "./KOLDrawerOutreachSection";
import { runSkill, type SkillRunResult } from "../../../../services/vkpi/skills-api";
import { candidateKindGroup } from "../lib/candidateKind";
import { kolHumanDisplayName } from "../lib/kolIdentity";
import { GoaffproLinkSection } from "../../shared/GoaffproLinkSection";
import {
  asArray,
  detailBundleAnalysisItems,
  detailBundleAnalysisSummary,
  dimensions11RadarDims,
  evidenceIdOf,
  isImageEvidence,
  recordOr,
  videoAnalysisPollDelayMs,
  videoAnalysisPollSnapshot,
  videoAnalysisSources,
} from "./KOLDetailDrawer.helpers";
import {
  AccountDossierPanel,
  CooperationPanel,
  LlmDeepAnalysisPanel,
  RepresentativeVideoPlayerModal,
} from "./KOLDetailDrawer.Panels";
import { SignaturePanel } from "./SignaturePanel";
import { FocalMatrixPanel } from "./FocalMatrixPanel";
import { QualityCompliancePanel } from "./QualityCompliancePanel";
import { SimilarVideosPanel } from "./SimilarVideosPanel";
import { ForecastPanel } from "./ForecastPanel";
import { RateCardPanel } from "./RateCardPanel";
import { AudienceGeoPanel } from "./AudienceGeoPanel";
import { OutreachCriticSignalCard } from "./OutreachCriticSignalCard";
import { SafetyAuthenticityPanel } from "./SafetyAuthenticityPanel";
import { KOLAnalysisTrustPanel } from "./KOLAnalysisTrustPanel";
import { videoAnalysisGateMessage } from "./KOLDetailDrawer.gates";
import { DRAWER_TABS, KOLDrawerBriefSkill, KOLDrawerCoopActions, KOLDrawerViewerContextBar, readStoredDrawerTab, storeDrawerTab } from "./KOLDetailDrawer.Subsections";
import { useKOLDrawerViewerContext } from "./useKOLDrawerViewerContext";
import { useKolDrawerContactState } from "../lib/useKolContactState";
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
export { videoAnalysisGateMessage } from "./KOLDetailDrawer.gates";

const e = React.createElement;

// 【C1 Tab 化】抽屉内容区四 tab:按「用户看数据的心智」把既有 section 分组渲染(零改各 section 内部)。
// 概览=这人是谁/分数为何/内容速览;深度分析=LLM/视频深析产物;受众=粉丝画像;合作=推进合作的动作面。
// 头部身份卡与底部粘性行动条在 tab 结构之外,全 tab 常驻。tab 选择记忆到 localStorage(跨 KOL/会话)。
export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact, staff = [], onReloadDetail }: any) {
  const contentFitProductSku = String(item?.product_sku || item?.productSku || "").trim();
  const { state: contactState, retry: retryContact, clear: clearContact } = useKolDrawerContactState(apiToken, item?.id);
  const handleClose = React.useCallback(() => {
    clearContact();
    onClose?.();
  }, [clearContact, onClose]);
  // P-GROUP-7 共享 KOL 池:把这条 My KOL(item.id = kol_pool_id)显式共享给成员(只读授予)。
  const [shareOpen, setShareOpen] = React.useState(false);
  // 【M3/M5】观看者上下文:共享来源(来自谁的共享)+ active 认领(本人/管理层可释放)。
  // 动态 import 保持 mock seam 不变;失败时不渲染徽标(graceful absence)。
  const { viewerCtx, releaseBusy, releaseMsg, handleReleaseClaim } = useKOLDrawerViewerContext({ apiToken, item });
  // 【C1】当前 tab:惰性读 localStorage(非法值回落「概览」);切换时写回记忆。
  const [activeTab, setActiveTab] = React.useState<string>(() => readStoredDrawerTab());
  const handleSelectTab = React.useCallback((key: string) => {
    setActiveTab(key);
    try {
      storeDrawerTab(key);
    } catch { /* 写失败不阻塞切换,本次会话内 state 仍生效 */ }
  }, []);
  const [dimensions11, setDimensions11] = React.useState<any>(null);
  const [llmDeepAnalysis, setLlmDeepAnalysis] = React.useState<any>(null);
  const [preloadedVideoAnalysisBundles, setPreloadedVideoAnalysisBundles] = React.useState<any>(undefined);
  const [videoAnalysisSummary, setVideoAnalysisSummary] = React.useState<any>(null);
  const [videoEnqueueState, setVideoEnqueueState] = React.useState<any>({ status: "idle", message: "" });
  // 全视频跑:该 KOL 全部视频证据各入队一条 final_v1,发完综合评估。独立于上面的单代表作入队。
  const [allVideosState, setAllVideosState] = React.useState<any>({ status: "idle", message: "" });
  const itemIdentity = String(item?.id || "");
  const itemIdentityRef = React.useRef(itemIdentity);
  itemIdentityRef.current = itemIdentity;
  type VideoPollMode = "single" | "all";
  const videoEnqueueGenerationRef = React.useRef<Record<VideoPollMode, number>>({ single: 0, all: 0 });
  const videoAnalysisPollRef = React.useRef<Record<VideoPollMode, { timer: ReturnType<typeof setTimeout> | null; generation: number; startedAt: number }>>({
    single: { timer: null, generation: 0, startedAt: 0 },
    all: { timer: null, generation: 0, startedAt: 0 },
  });
  const videoReadyCountRef = React.useRef(0);
  const clearVideoAnalysisPoll = React.useCallback((mode?: VideoPollMode) => {
    const modes: VideoPollMode[] = mode ? [mode] : ["single", "all"];
    modes.forEach((key) => {
      const controller = videoAnalysisPollRef.current[key];
      if (controller.timer) clearTimeout(controller.timer);
      controller.timer = null;
      controller.generation += 1;
    });
  }, []);
  const reloadDetailSafely = React.useCallback(async () => {
    if (typeof onReloadDetail !== "function") return;
    try {
      await onReloadDetail();
    } catch {
      // 下一轮仍可继续；真实错误由详情加载态展示，不在这里伪造终态。
    }
  }, [onReloadDetail]);
  const startVideoAnalysisPoll = React.useCallback((options: {
    evidenceId?: number | null;
    evidenceIds?: number[];
    targetReady?: number | null;
    initialReady?: number;
    initialTerminal?: number;
    terminalReasons?: string[];
    itemIdentity: string;
    mode: VideoPollMode;
  }) => {
    if (
      !apiToken
      || !options.itemIdentity
      || itemIdentityRef.current !== options.itemIdentity
      || typeof onReloadDetail !== "function"
    ) return false;
    clearVideoAnalysisPoll(options.mode);
    const controller = videoAnalysisPollRef.current[options.mode];
    const generation = controller.generation;
    const startedAt = Date.now();
    const pendingEvidenceIds = new Set((options.evidenceIds || []).filter((id) => Number.isFinite(id) && id > 0));
    let readyCount = Math.max(0, Number(options.initialReady || 0));
    let terminalCount = Math.max(0, Number(options.initialTerminal || 0));
    const terminalReasons = [...(options.terminalReasons || [])];
    controller.startedAt = startedAt;
    const isCurrent = () => (
      generation === controller.generation
      && itemIdentityRef.current === options.itemIdentity
    );
    const schedule = (callback: () => void) => {
      const elapsed = Date.now() - startedAt;
      controller.timer = setTimeout(callback, videoAnalysisPollDelayMs(elapsed));
    };
    const poll = async () => {
      if (!isCurrent()) return;
      let shouldReloadDetail = false;
      const elapsed = Date.now() - startedAt;
      if (elapsed >= 30 * 60_000) {
        clearVideoAnalysisPoll(options.mode);
        const message = "后台任务仍未形成终态，已停止自动刷新；可点“刷新状态”继续核验。";
        if (options.mode === "single") setVideoEnqueueState({ status: "paused", message });
        else setAllVideosState({ status: "paused", message });
        return;
      }
      if (options.mode === "single" && options.evidenceId) {
        try {
          const payload = await getKolVideoAnalysisCache(
            apiToken,
            options.evidenceId,
            "video_analysis_final_v1",
            { allowLocalEvaluationFallback: false },
          );
          if (!isCurrent()) return;
          const snapshot = videoAnalysisPollSnapshot(payload);
          const state = snapshot.state;
          if (state === "ready") {
            await reloadDetailSafely();
            if (!isCurrent()) return;
            clearVideoAnalysisPoll(options.mode);
            setVideoEnqueueState({ status: "already_analyzed", message: "视频深析已完成并自动回填。" });
            return;
          }
          if (snapshot.terminal) {
            await reloadDetailSafely();
            if (!isCurrent()) return;
            clearVideoAnalysisPoll(options.mode);
            setVideoEnqueueState({ status: state, message: `视频深析未完成：${snapshot.reason}` });
            return;
          }
        } catch {
          // 短暂读失败不冒充任务失败；继续有界重试。
        }
      } else if (options.mode === "all" && pendingEvidenceIds.size > 0) {
        const evidenceIds = [...pendingEvidenceIds];
        let batchItems: any[] = [];
        try {
          const batch = await getKolVideoAnalysisBatch(
            apiToken,
            evidenceIds,
            "video_analysis_final_v1",
          );
          batchItems = Array.isArray(batch?.items) ? batch.items : [];
        } catch {
          // 短暂读失败不冒充任务失败；继续有界重试。
        }
        if (!isCurrent()) return;
        batchItems.forEach((payload) => {
          const snapshot = videoAnalysisPollSnapshot(payload);
          const evidenceId = snapshot.evidenceId;
          if (!pendingEvidenceIds.has(evidenceId)) return;
          if (snapshot.ready) {
            pendingEvidenceIds.delete(evidenceId);
            readyCount += 1;
            shouldReloadDetail = true;
          } else if (snapshot.terminal) {
            pendingEvidenceIds.delete(evidenceId);
            terminalCount += 1;
            terminalReasons.push(snapshot.reason);
            shouldReloadDetail = true;
          }
        });
        if (pendingEvidenceIds.size === 0) {
          await reloadDetailSafely();
          if (!isCurrent()) return;
          clearVideoAnalysisPoll(options.mode);
          const terminalSuffix = terminalCount
            ? `，${terminalCount} 条未完成${terminalReasons.length ? `（${[...new Set(terminalReasons)].slice(0, 3).join("、")}）` : ""}`
            : "";
          setAllVideosState({
            status: terminalCount ? "partial" : "done",
            message: `全视频深析终态：${readyCount} 条就绪${terminalSuffix}。`,
          });
          return;
        }
      } else if (options.targetReady && videoReadyCountRef.current >= options.targetReady) {
        clearVideoAnalysisPoll(options.mode);
        setAllVideosState({ status: "done", message: `已自动回填 ${videoReadyCountRef.current} 条视频深析结果。` });
        return;
      } else if (options.targetReady) {
        // 旧服务端没有返回 evidence id 时，只能通过重拉 detail
        // 观察 ready_count；新服务端的有界 batch 路径不需要空转重拉。
        shouldReloadDetail = true;
      }
      if (shouldReloadDetail) await reloadDetailSafely();
      if (isCurrent()) schedule(() => void poll());
    };
    if (isCurrent()) void reloadDetailSafely();
    schedule(() => void poll());
    return true;
  }, [apiToken, onReloadDetail, clearVideoAnalysisPoll, reloadDetailSafely]);
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
  // 后台抓评论完成后自动无痛刷新:pending_comments 入队后每 25s 静默重拉 detail_bundle,最多 20 次。
  // interval id 存 ref(不进 state,避免无谓重渲染);卸载/切 KOL/再手动刷新时都要先清旧表。
  const audiencePollRef = React.useRef<any>(null);
  const clearAudiencePoll = React.useCallback(() => {
    if (audiencePollRef.current) {
      clearInterval(audiencePollRef.current);
      audiencePollRef.current = null;
    }
  }, []);
  const handleRefreshAudience = React.useCallback(() => {
    if (!apiToken || !item?.id) return;
    // 再次手动刷新时先清旧轮询,避免双计时器叠加打接口。
    clearAudiencePoll();
    setAudienceState({ status: "loading", message: "" });
    void refreshAudienceStats(apiToken, item.id)
      .then((payload: any) => {
        const status = String(payload?.status || "");
        if (status === "ok") {
          setAudienceState({ status: "done", message: `已更新:样本 ${payload?.sample_size ?? "—"} 评论者 · 置信 ${payload?.confidence ?? "—"}` });
          // 完成后重拉 detail_bundle,让面板吃到新 audience_estimated。
          if (typeof onReloadDetail === "function") void onReloadDetail();
        } else if (status === "pending_comments") {
          const enqueued = Boolean(payload?.enqueued);
          if (enqueued && typeof onReloadDetail === "function") {
            // 已入队抓评论 → 起轮询:每 25s 重拉一次 detail_bundle,上限 20 次(≈8 分钟)。
            // 轮询回调里读不到最新 bundle(闭包旧值),所以无条件轮询到上限;
            // 数据一旦出现由下方观察 item.audience_estimated 的 effect 提前停表。
            let ticks = 0;
            audiencePollRef.current = setInterval(() => {
              ticks += 1;
              if (ticks > 20) {
                clearAudiencePoll();
                return;
              }
              void onReloadDetail();
            }, 25000);
            setAudienceState({ status: "pending", message: "评论抓取中 — 完成后本页自动出数据" });
          } else {
            setAudienceState({
              status: "pending",
              message: enqueued ? "评论不足,已入队抓评论 — 可稍后再刷新" : String(payload?.reason || "评论不足,稍后再试"),
            });
          }
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
  }, [apiToken, item?.id, onReloadDetail, clearAudiencePoll]);
  // 轮询期间一旦 detail_bundle 带回受众数据(item.audience_estimated 出现),立刻停表并提示就绪;
  // 没在轮询时(ref 为空)不动 audienceState,避免覆盖手动刷新的提示文案。
  React.useEffect(() => {
    const est = item?.audience_estimated;
    const hasData = Boolean(est && typeof est === "object" && Number((est as any).sample_size || 0) > 0);
    if (hasData && audiencePollRef.current) {
      clearAudiencePoll();
      setAudienceState({ status: "done", message: "受众数据已就绪(后台抓取完成)" });
    }
  }, [item?.audience_estimated, clearAudiencePoll]);
  // 组件卸载或切换 KOL 时清掉受众轮询,避免对已关闭抽屉/上一条 KOL 持续打接口。
  React.useEffect(() => {
    return () => clearAudiencePoll();
  }, [item?.id, clearAudiencePoll]);
  // Profile 深爬只能由显式按钮触发；入队成功后 30s×10 静默重拉 detail_bundle 等档案落地。
  const profileCrawlRef = React.useRef<any>({ timer: null });
  // 【A2/A3】抓取态:idle/loading(入队请求中)/crawling(已入队,轮询等落地)/done/error。
  const [profileCrawlState, setProfileCrawlState] = React.useState<any>({ status: "idle", message: "" });
  // 30s×10 轮询 onReloadDetail 等抓取落地;已有表在跑就不叠表。返回是否真的起了轮询(无 onReloadDetail 时 false)。
  const startCrawlPolling = React.useCallback(() => {
    if (typeof onReloadDetail !== "function") return false;
    if (profileCrawlRef.current.timer) return true;
    let ticks = 0;
    profileCrawlRef.current.timer = setInterval(() => {
      ticks += 1;
      if (ticks > 10) {
        clearInterval(profileCrawlRef.current.timer);
        profileCrawlRef.current.timer = null;
        // 轮询到点还没等到证据:不装完成,如实告知后台仍在跑。
        setProfileCrawlState((prev: any) => prev?.status === "crawling"
          ? { status: "idle", message: "抓取仍在后台进行 — 几分钟后重开抽屉查看" }
          : prev);
        return;
      }
      void onReloadDetail();
    }, 30000);
    return true;
  }, [onReloadDetail]);
  // 【A1/A2】显式入队 profile 深爬；提交 KOL 已存主页，由安全 endpoint 校验目标可写、
  // 规范 URL 与 worker fence 后才允许进入 provider 队列。
  const startProfileCrawl = React.useCallback(() => {
    const id = item?.id;
    const profileUrl = String(item?.profile_url || "").trim();
    if (!apiToken || !id) return;
    if (!profileUrl) {
      setProfileCrawlState({ status: "error", message: "该 KOL 缺主页链接(profile_url),无法抓取" });
      return;
    }
    setProfileCrawlState({ status: "loading", message: "" });
    void enqueueKolProfileCrawl(apiToken, id, profileUrl)
      .then((res: any) => {
        const status = String(res?.status || "");
        if (status === "queued" || status === "already_queued") {
          const polling = startCrawlPolling();
          setProfileCrawlState({
            status: "crawling",
            message: (status === "already_queued" ? "已在抓取队列中" : "已入队抓取(account_deep)")
              + (polling ? " — 完成后本页自动出现(约几分钟)" : " — 几分钟后重开抽屉查看"),
          });
        } else if (status === "already_has_evidence") {
          setProfileCrawlState({ status: "done", message: "该 KOL 已有视频证据 — 正在刷新详情" });
          if (typeof onReloadDetail === "function") void onReloadDetail();
        } else {
          setProfileCrawlState({
            status: "error",
            message: status === "no_profile_url" ? "该 KOL 缺主页链接(profile_url),无法抓取"
              : status === "not_found" ? "该 KOL 在池内不存在(可能已被清理)"
              : "抓取入队失败:" + (status || "未知返回"),
          });
        }
      })
      .catch((error: any) => {
        setProfileCrawlState({ status: "error", message: error?.message ? String(error.message) : "抓取入队失败" });
      });
  }, [apiToken, item?.id, item?.profile_url, onReloadDetail, startCrawlPolling]);
  // 抓取轮询清表独立成 effect(仅按 item.id 键控):触发 effect 依赖里有 detailBundle,
  // 若把清表挂它的 cleanup,轮询每次重拉 bundle 都会误杀自己的 30s 计时器。
  React.useEffect(() => {
    return () => {
      if (profileCrawlRef.current.timer) {
        clearInterval(profileCrawlRef.current.timer);
        profileCrawlRef.current.timer = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id]);
  // 轮询期间一旦 detail_bundle 带回视频证据 → 立刻停表 + 提示就绪(与受众轮询同款观察者)。
  React.useEffect(() => {
    const bundle = recordOr(detailBundle);
    if (bundle.status !== "ready") return;
    const hasEvidence = asArray(recordOr(bundle.item).video_evidence).length > 0
      || detailBundleAnalysisItems(bundle).length > 0;
    if (hasEvidence && profileCrawlRef.current.timer) {
      clearInterval(profileCrawlRef.current.timer);
      profileCrawlRef.current.timer = null;
      setProfileCrawlState({ status: "done", message: "视频证据已就绪(后台抓取完成)" });
      // 证据已到:清掉「无证据」残留态,深析按钮恢复为全视频入队入口。
      setAllVideosState((prev: any) => (prev?.status === "no_evidence" ? { status: "idle", message: "" } : prev));
    }
  }, [detailBundle]);
  // v2 证据下钻:面板内可展开块(intent/brands/fans)单开手风琴;state 在父层,子组件仍零 state。
  const [audienceExpand, setAudienceExpand] = React.useState<string | null>(null);
  const handleToggleAudienceBlock = React.useCallback((key: string) => {
    setAudienceExpand((prev) => (prev === key ? null : key));
  }, []);
  // 发现即建档·手动升级:一键补全档案(强制 full 档:深爬+评论,深析/受众链自动跟;幂等)。
  const [buildFullState, setBuildFullState] = React.useState<"idle" | "loading" | "done" | "error">("idle");
  const onBuildFullProfile = React.useCallback(async () => {
    if (!apiToken || !item?.id || buildFullState === "loading") return;
    setBuildFullState("loading");
    try {
      const { buildFullKolProfile } = await import("../../../../services/vkpi/kolPool-api");
      await buildFullKolProfile(apiToken, Number(item.id));
      setBuildFullState("done");
    } catch {
      setBuildFullState("error");
    }
  }, [apiToken, item?.id, buildFullState]);
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
    if (detailLoading) {
      // detail_bundle 在途(其自带 dimensions11/llm):先等 bundle,不抢发两条注定被覆盖的独立请求;
      // bundle 失败时 detailLoading 翻 false 且 bundle 仍空 → effect 重跑走下方兜底拉取。
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
  }, [apiToken, item?.id, detailBundle, detailLoading]);

  React.useEffect(() => {
    const ready = Number(recordOr(videoAnalysisSummary).ready_count);
    videoReadyCountRef.current = Number.isFinite(ready) ? ready : 0;
  }, [videoAnalysisSummary]);

  React.useEffect(() => () => clearVideoAnalysisPoll(), [item?.id, clearVideoAnalysisPoll]);

  React.useEffect(() => {
    videoEnqueueGenerationRef.current.single += 1;
    videoEnqueueGenerationRef.current.all += 1;
    setVideoEnqueueState({ status: "idle", message: "" });
    setAllVideosState({ status: "idle", message: "" });
    videoReadyCountRef.current = 0;
    setActiveRepresentativeVideo(null);
    // 换 KOL 时清掉上一条的 brief skill 结果,避免串号。
    setBriefResult(null);
    setBriefError("");
    setBriefBusy(false);
    // 换 KOL 时受众刷新态/证据展开态归零,避免上一条的 loading/报错/展开串号。
    setAudienceState({ status: "idle", message: "" });
    setAudienceExpand(null);
    // 换 KOL 时抓取态归零(抽屉带 key 通常会整体重挂,这里兜底防串号)。
    setProfileCrawlState({ status: "idle", message: "" });
  }, [item?.id]);

  // 内容契合深析:开抽屉先只读已有缓存(不烧 LLM);无缓存则留待用户点击触发。
  React.useEffect(() => {
    setContentFit(null);
    setContentFitError("");
    setContentFitBusy(false);
    if (!apiToken || !item?.id) return;
    let cancelled = false;
    void getKolPoolContentFit(
      apiToken,
      item.id,
      contentFitProductSku ? { productSku: contentFitProductSku } : {},
    )
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
  }, [apiToken, item?.id, contentFitProductSku]);

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
    if (
      !apiToken
      || !item?.id
      || contentFitBusy
      || viewerCtx?.paid_actions?.can_run_paid_actions !== true
    ) return;
    setContentFitBusy(true);
    setContentFitError("");
    void analyzeKolPoolContentFit(apiToken, item.id, {
      force,
      ...(contentFitProductSku ? { productSku: contentFitProductSku } : {}),
    })
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
      .catch((error: any) => setContentFitError(
        Number(error?.status || 0) === 403
          ? "请先关注该 KOL；共享条目仅可查看，不能发起付费深析。"
          : "深析请求失败,请稍后重试。",
      ))
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
      .catch((err: any) => {
        // 人话化:/skills/{id}/run 是本地分支功能,线上后端多半没有该路由(404)→ 如实告知,不甩原始报错。
        const status = err && typeof err.status === "number" ? err.status : 0;
        const raw = err?.message ? String(err.message) : "";
        setBriefError(
          status === 404 || /404|not found/i.test(raw)
            ? "Skill 后端未上线(本地分支功能),brief 草案暂不可用。"
            : (raw || "跑 Skill 失败,请稍后重试。"),
        );
      })
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
  // 【K4】主代表作跳过 image 类 evidence(IG 图文/轮播):detail_bundle 现在会回带 image 行,
  // 但视频深析只能吃 video——若首条是 image,取第一条非 image 的做「AI深度分析」入队目标,
  // 避免把图文帖排进视频分析(后端另有 skipped_non_video 闸兜底)。
  const primaryVideoEvidence = videoAnalysisVideos.find((video: any) => !isImageEvidence(video)) || null;
  const primaryVideoEvidenceId = evidenceIdOf(primaryVideoEvidence);
  const videoEnqueueBusy = videoEnqueueState.status === "loading" || videoEnqueueState.status === "queued" || videoEnqueueState.status === "already_queued";
  const canEnqueueVideoAnalysis = Boolean(apiToken && item.id && primaryVideoEvidenceId && !videoEnqueueBusy);
  const videoEnqueueLabel =
    videoEnqueueState.status === "loading" ? "入队中…" :
    videoEnqueueState.status === "queued" ? "分析中…" :
    videoEnqueueState.status === "already_analyzed" ? "已分析过" :
    videoEnqueueState.status === "already_queued" ? "已在队列中" :
    videoEnqueueState.status === "ai_disabled" || videoEnqueueState.status === "not_requested" ? "AI暂不可用" :
    "AI深度分析";
  const videoEnqueueTitle =
    videoEnqueueState.message ||
    (primaryVideoEvidenceId
      ? "入队当前主代表作 evidence #" + primaryVideoEvidenceId + "；任务进度会显示在左侧看板"
      : "暂无可分析的 video evidence");
  const handleVideoAnalysisEnqueue = () => {
    if (!apiToken || !item.id || !primaryVideoEvidenceId || videoEnqueueBusy) return;
    clearVideoAnalysisPoll("single");
    const operationItemIdentity = String(item.id);
    const operationGeneration = videoEnqueueGenerationRef.current.single + 1;
    videoEnqueueGenerationRef.current.single = operationGeneration;
    const isCurrentOperation = () => (
      itemIdentityRef.current === operationItemIdentity
      && videoEnqueueGenerationRef.current.single === operationGeneration
    );
    setVideoEnqueueState({ status: "loading", message: "正在把 evidence #" + primaryVideoEvidenceId + " 加入深析队列" });
    void enqueueVideoAnalysis(apiToken, item.id, primaryVideoEvidenceId)
      .then((payload) => {
        if (!isCurrentOperation()) return;
        const status = String(payload?.status || "");
        if (status === "queued") {
          setVideoEnqueueState({ status, message: "已入队；左侧任务进度看板会自动显示" });
          startVideoAnalysisPoll({ evidenceId: primaryVideoEvidenceId, itemIdentity: operationItemIdentity, mode: "single" });
        } else if (status === "already_analyzed") {
          setVideoEnqueueState({ status, message: "这条 evidence 已有 final_v1 深析结果" });
          void reloadDetailSafely();
        } else if (status === "already_queued") {
          setVideoEnqueueState({ status, message: "这条 evidence 已在队列中，避免重复入队" });
          startVideoAnalysisPoll({ evidenceId: primaryVideoEvidenceId, itemIdentity: operationItemIdentity, mode: "single" });
        } else if (status === "ai_disabled" || status === "not_requested") {
          setVideoEnqueueState({ status: "ai_disabled", message: videoAnalysisGateMessage(payload) });
        } else if (status === "budget_denied") {
          setVideoEnqueueState({ status, message: "预算闸门拒绝，本次未入队" });
        } else {
          setVideoEnqueueState({ status: "error", message: payload?.message || payload?.reason || "入队失败" });
        }
      })
      .catch((error) => {
        if (!isCurrentOperation()) return;
        const message = error?.message ? String(error.message) : "入队失败";
        setVideoEnqueueState({ status: "error", message });
      });
  };
  const allVideosBusy = ["loading", "processing", "queued", "already_queued"].includes(allVideosState.status);
  // 【A2】抓取按钮态:入队请求中/轮询中都算忙(按钮 disabled + 文案「抓取中…」)。
  const crawlBusy = profileCrawlState.status === "loading" || profileCrawlState.status === "crawling";
  // 「无视频证据」判定:detail_bundle 已到 → 以 item.video_evidence 与 video_analysis.items 双空为准;
  // bundle 未到时以 enqueueAllKolVideos 返回过 no_evidence 兜底(后端查的是 vkpi_kol_video_evidence 真表)。
  // bundle 就绪后不再信残留的 no_evidence 态(抓取落地/换水后按钮要能立即恢复全视频入队)。
  const bundleRecordForEvidence = recordOr(detailBundle);
  const bundleReadyForEvidence = bundleRecordForEvidence.status === "ready";
  const bundleNoEvidence = bundleReadyForEvidence
    && asArray(recordOr(bundleRecordForEvidence.item).video_evidence).length === 0
    && detailBundleAnalysisItems(bundleRecordForEvidence).length === 0;
  const noEvidenceKnown = bundleReadyForEvidence ? bundleNoEvidence : allVideosState.status === "no_evidence";
  const handleEnqueueAllVideos = () => {
    if (!apiToken || !item.id || allVideosBusy) return;
    clearVideoAnalysisPoll("all");
    const operationItemIdentity = String(item.id);
    const operationGeneration = videoEnqueueGenerationRef.current.all + 1;
    videoEnqueueGenerationRef.current.all = operationGeneration;
    const isCurrentOperation = () => (
      itemIdentityRef.current === operationItemIdentity
      && videoEnqueueGenerationRef.current.all === operationGeneration
    );
    setAllVideosState({ status: "loading", message: "正在把该 KOL 的全部视频证据加入深析队列…" });
    void enqueueAllKolVideos(apiToken, item.id)
      .then((payload: any) => {
        if (!isCurrentOperation()) return;
        const status = String(payload?.status || "");
        if (status === "no_evidence") {
          // 【A2】后端确认无证据 → 自动转为抓取(account_deep),不让用户对着死路按钮干瞪眼。
          setAllVideosState({ status: "no_evidence", message: "该 KOL 暂无视频证据 — 已自动转为抓取(account_deep 模式)" });
          startProfileCrawl();
          return;
        }
        const queued = Number(payload?.queued || 0);
        const skipped = Number(payload?.skipped || 0);
        const total = Number(payload?.evidence_total || payload?.requested || 0);
        const items = Array.isArray(payload?.items) ? payload.items : [];
        const activeEvidenceIds: number[] = [];
        let initialReady = 0;
        let initialTerminal = 0;
        const terminalReasons: string[] = [];
        items.forEach((rawItem: any) => {
          const resultStatus = String(rawItem?.status || "").toLowerCase();
          const evidenceId = Number(rawItem?.evidence_id || 0);
          if (["queued", "already_queued"].includes(resultStatus) && evidenceId > 0) {
            activeEvidenceIds.push(evidenceId);
          } else if (["already_analyzed", "already_evaluated"].includes(resultStatus)) {
            initialReady += 1;
          } else if ([
            "ai_disabled",
            "budget_denied",
            "unsupported_platform",
            "skipped_non_video",
            "invalid_item",
            "not_found",
            "error",
            "blocked",
            "failed",
          ].includes(resultStatus)) {
            initialTerminal += 1;
            terminalReasons.push(String(rawItem?.reason || rawItem?.provider_gate_reason || resultStatus));
          }
        });
        if (activeEvidenceIds.length > 0) {
          setAllVideosState({
            status: "processing",
            message: `全视频跑：共 ${total} 条，${activeEvidenceIds.length} 条处理中，${initialReady} 条已有结果，${initialTerminal} 条已终止。`,
          });
          startVideoAnalysisPoll({
            evidenceIds: [...new Set(activeEvidenceIds)],
            initialReady,
            initialTerminal,
            terminalReasons,
            itemIdentity: operationItemIdentity,
            mode: "all",
          });
        } else if (items.length > 0) {
          const terminalSuffix = initialTerminal
            ? `，${initialTerminal} 条未完成${terminalReasons.length ? `（${[...new Set(terminalReasons)].slice(0, 3).join("、")}）` : ""}`
            : "";
          setAllVideosState({
            status: initialTerminal ? "partial" : "done",
            message: `全视频深析终态：${initialReady} 条就绪${terminalSuffix}。`,
          });
          void reloadDetailSafely();
        } else if (queued > 0) {
          // 兼容尚未回传 items 的旧服务端；新服务端按 evidence id 逐条收敛。
          setAllVideosState({ status: "processing", message: `全视频跑:共 ${total} 条,入队 ${queued},跳过 ${skipped};正在核验终态。` });
          startVideoAnalysisPoll({
            targetReady: videoReadyCountRef.current + queued,
            itemIdentity: operationItemIdentity,
            mode: "all",
          });
        } else {
          setAllVideosState({ status: "done", message: `全视频跑:共 ${total} 条,入队 ${queued},跳过 ${skipped}。` });
          void reloadDetailSafely();
        }
      })
      .catch((error: any) => {
        if (!isCurrentOperation()) return;
        setAllVideosState({ status: "error", message: error?.message ? String(error.message) : "全视频入队失败" });
      });
  };
  const dimensions11Dims = dimensions11RadarDims(dimensions11);
  // 【C4 新发现预设折叠】新发现/校验中候选(new_promoted/new_validated/new_discovered)默认收起
  // V6 Breakdown/适配判断/产品线/风险/合作历史(这些块对新候选多为空占位);受众和设备保持展开。
  // SectionFold 先读 localStorage,用户手动折叠过的记忆优先于该默认值。
  const isNewCandidate = candidateKindGroup(item.candidate_kind) === "new";
  const foldDefaultOpen = !isNewCandidate;
  const v6Breakdown = item.v6_breakdown && typeof item.v6_breakdown === "object"
    ? item.v6_breakdown
    : item.score_breakdown && typeof item.score_breakdown === "object"
      ? item.score_breakdown
      : null;
  
  return e(React.Fragment, null,
  e(m.div, {
    initial: { x: "100%" }, animate: { x: 0 }, exit: { x: "100%" },
    transition: { type: "spring", damping: 28, stiffness: 240 },
    "aria-label": "KOL Pool 详情",
    className: "vkpi-kol-detail-drawer fixed top-0 right-0 h-full w-[520px] bg-[#0a1020] border-l border-white/[0.08] shadow-2xl z-50 flex flex-col"
  },
    // ─── Header ───
    e(KOLDrawerHeader, { item, devices, detailLoading, detailError, onClose: handleClose }),

    // ── 【M3/M5】观看者上下文条:来自谁的共享 + 认领状态/释放(有数据才渲染,全 tab 常驻)──
    e(KOLDrawerViewerContextBar, {
      viewerCtx,
      releaseBusy,
      releaseMsg,
      onRelease: handleReleaseClaim,
    }),

    // ─── Scroll content ───
    // 【C1 Tab 化】既有 section 一列不变,只按 activeTab 过滤渲染对应集合(不动各 section 内部);
    // tab 条 sticky 钉在滚动区顶部;头部身份卡(上方)与底部行动条(下方)在滚动区之外全 tab 常驻。
    e("div", { className: "flex-1 overflow-y-auto" },
      // ── Tab 条(sticky,不透明底遮住滚过的内容)──
      e("div", { className: "sticky top-0 z-30 flex items-center gap-1 border-b border-white/[0.08] bg-[#0a1020] px-5 py-2" },
        DRAWER_TABS.map((tab) => e("button", {
          key: tab.key,
          type: "button",
          onClick: () => handleSelectTab(tab.key),
          "aria-pressed": activeTab === tab.key,
          className: "rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors " + (
            activeTab === tab.key
              ? "bg-white/[0.10] text-white"
              : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-200"
          ),
        }, tab.label))
      ),

      // ══ Tab「概览」:这人是谁 + 分数为何 + 内容/联系速览 ══
      activeTab === "overview" && e(React.Fragment, null,
        // ── Bio ──
        item.bio && e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
          e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, item.bio)
        ),
        // ── Why V6 Fit = N? · 速读 4 bullets(规则生成) ──
        v6Breakdown && e(KOLDrawerWhyFitCard, { v6Breakdown, loyaltySignals, geoDistribution, trendHits, item, devices, competitorCollabs, potentialConcerns }),
        // ── 3-card grid: Real ER / Geo / Loyalty(B6:Trend 卡已诚实摘除;C3:全空时组件内合并成一行淡字)──
        e(KOLDrawerMetricGrid, { item, loyaltySignals }),
        // ── 11 维度雷达: persisted backend dimensions_11_json only ──
        dimensions11Dims.length > 0 && e(KOLDrawerRadar11, { dims: dimensions11Dims, dimensions11 }),
        // ── V6 Fit Breakdown(与 why-fit 同属「分数为何」心智,归概览)──
        e(KOLDrawerV6Breakdown, { item, v6Breakdown, foldDefaultOpen }),
        // ── Trend hits(真实命中列表,非 B6 摘除的恒空评分卡)──
        trendHits.length > 0 && e(KOLDrawerTrendHits, { trendHits }),
        // ── 联系方式 & 代表视频(身份速览的一部分:是谁 + 内容长啥样)──
        e(KOLDrawerContactAndVideos, {
          item, representativeVideos, contactState,
          onOpenVideo: setActiveRepresentativeVideo, onRetryContact: retryContact,
        }),
        // ── 推荐动作/文本区块: Viltrox 适配判断 / 推荐产品线 / 风险点 / 品牌合作历史 ──
        e(KOLDrawerTextSections, { item, recommendedProductLines, potentialConcerns, brandCollaborations, competitorCollabs, foldDefaultOpen }),
      ),

      // ══ Tab「深度分析」:视频深析产物 + LLM 判断 + 档案 + 内容契合 ══
      activeTab === "deep" && e(React.Fragment, null,
        // 先给出证据就绪、覆盖、关键样本与声明边界；覆盖度不是预测准确率。
        e(KOLAnalysisTrustPanel, {
          detailBundle,
          item,
          videoAnalysisSummary,
        }),
        // 【A3】抓取中把轮询态透传给深析结果区标题旁的小徽标(复用组件内已有轮询态,零全局依赖)。
        e(KOLVideoAnalysisPanel, {
          apiToken,
          videos: videoAnalysisVideos,
          preloadedBundles: preloadedVideoAnalysisBundles,
          summary: videoAnalysisSummary,
          crawling: crawlBusy,
          analysisState: videoEnqueueState,
          onRefresh: reloadDetailSafely,
        }),
        // ── 全视频跑:该 KOL 全部视频证据各入队一条 final_v1 → 发完综合评估 ──
        // 【A2】无 evidence 时本按钮变「一键抓取」:点击入队 profile 深爬(account_deep 级联),
        // 按钮态「抓取中…」+ 30s×10 轮询 onReloadDetail 等证据落地;已入队/已有证据都给人话反馈。
        apiToken && item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
          e("button", {
            type: "button",
            disabled: allVideosBusy || (noEvidenceKnown && crawlBusy),
            onClick: noEvidenceKnown ? startProfileCrawl : handleEnqueueAllVideos,
            className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-200 transition-colors hover:bg-cyan-400/[0.12] disabled:opacity-50",
          },
            e(Sparkles, { size: 12 }),
            noEvidenceKnown
              ? (crawlBusy ? "抓取中…" : "KOL深度分析理解(最近20条)")
              : (allVideosBusy ? "深度分析入队中…" : "KOL深度分析理解(最近20条)")
          ),
          allVideosState.message && e("div", {
            className: "mt-1.5 text-[9.5px] leading-relaxed " + (allVideosState.status === "error" ? "text-rose-300" : allVideosState.status === "no_evidence" ? "text-amber-300" : "text-slate-500")
          }, allVideosState.message),
          // 抓取反馈行(A2):queued/already_queued/already_has_evidence/错误的人话文案。
          profileCrawlState.message && e("div", {
            className: "mt-1.5 text-[9.5px] leading-relaxed " + (profileCrawlState.status === "error" ? "text-rose-300" : profileCrawlState.status === "crawling" ? "text-amber-300" : "text-slate-400")
          }, profileCrawlState.message)
        ),
        e(LlmDeepAnalysisPanel, { payload: llmDeepAnalysis }),
        // ── item4 账号档案(本地聚合,零 LLM):覆盖度/缺口/账号级判断/最近事件 ──
        e(AccountDossierPanel, { apiToken, kolPoolId: item?.id }),
        // ── 第2轮 招牌内容画像(零 LLM 纯聚合):擅长拍法 / 最爆 TOP3 / 最引反馈 ──
        e(SignaturePanel, { apiToken, kolPoolId: item?.id }),
        // ── 第3轮 信号聚合:焦段矩阵(覆盖+空白可切入)/ 质量分+FTC披露 / 以主代表作找相似 ──
        e(FocalMatrixPanel, { apiToken, kolPoolId: item?.id }),
        e(QualityCompliancePanel, { apiToken, kolPoolId: item?.id }),
        // G4 品牌安全 v0 + 受众真实性(库内信号,诚实标外网扫描待接)
        e(SafetyAuthenticityPanel, { apiToken, kolPoolId: item?.id }),
        e(SimilarVideosPanel, { apiToken, evidenceId: primaryVideoEvidenceId }),
        // ── 第4轮 商业档案:预测战绩(分位数区间+置信度)/ 报价卡(估价+录入) ──
        e(ForecastPanel, { apiToken, kolPoolId: item?.id }),
        e(RateCardPanel, { apiToken, kolPoolId: item?.id }),
        // 地基B:内容契合深析(content_fit_v1)——基于视频画面/故事 + 评论的适配判断(胜过粉丝数)。
        e(KOLDrawerContentFit, { apiToken, item, contentFit,
          contentFitBusy,
          contentFitError,
          canAnalyze: viewerCtx?.paid_actions?.can_run_paid_actions === true,
          onAnalyze: handleContentFitAnalyze,
        }),
      ),

      // ══ Tab「受众」:粉丝画像 + 设备 ══
      activeTab === "audience" && e(React.Fragment, null,
        // ── Audience Stats·估算 BETA + 受众语言 + 创作者所在地(组件内部自判空;无数据但可刷新时也渲染)──
        e(KOLDrawerGeoDistribution, {
          item, geoDistribution, apiToken, audienceState,
          onRefreshAudience: handleRefreshAudience,
          audienceExpand, onToggleAudienceBlock: handleToggleAudienceBlock,
        }),
        // B2 受众地图 v1:多信号融合(评论语言/文字系统/档案国别/创作者国弱先验)+代理口径角标
        e(AudienceGeoPanel, { apiToken, kolPoolId: item?.id }),
        e(KOLDrawerDevices, { item, devices }),
      ),

      // ══ Tab「合作」:推进合作的动作面 ══
      activeTab === "coop" && e(React.Fragment, null,
        e(CooperationPanel, { apiToken, kolPoolId: item?.id }),
        // ── C4 外联区:一键生成 brief + 中英双语邮件草稿 + 邮箱补抓状态(复制不外发)──
        e(KOLDrawerOutreachSection, { apiToken, kolPoolId: item?.id }),
        // G1 敢给差评信号 + 三承诺卡(打行业拖款/借测/黑名单三大怨气)
        e(OutreachCriticSignalCard, { apiToken, kolPoolId: item?.id }),
        // ── 长期记忆(W3)· 显式独立于 V6 Fit · 不影响排序 ──
        // 红线:本区块纯渲染聚合记忆,绝不渲染任何 viltrox/v6_fit 数值。
        e(KOLDrawerMemorySection, { kolMemory }),
        // ── N2 跑 Skill:brief_generate → 合作 brief 草案(默认走规则模板,不烧 LLM;红线零触 fit)──
        apiToken && item?.id && e(KOLDrawerBriefSkill, {
          result: briefResult, busy: briefBusy, error: briefError, onRun: handleRunBriefSkill,
        }),
        // ── D2 生成追踪链(GOAFFPRO):一键给该 KOL 建 affiliate + 追踪链 + 优惠码(KOL 零注册)──
        e(GoaffproLinkSection, { apiToken, kolPoolId: item?.id }),
        // ── P-GROUP-7 共享成员 + 查看完整档案：纯行动区抽出，状态/路由契约不变。──
        e(KOLDrawerCoopActions, { apiToken, item, onOpenShare: () => setShareOpen(true) }),
      ),
    ),
    // ─── Footer actions ───
    e(KOLDrawerFooter, {
      item, inMyList, onToggleMyList,
      onContact: (selected: any) => onContact?.(selected, contactState), contactState,
      onPromote,
      promoteMsg,
      canEnqueueVideoAnalysis, videoEnqueueLabel, videoEnqueueTitle, videoEnqueueState,
      onEnqueueVideoAnalysis: handleVideoAnalysisEnqueue,
      buildFullState, onBuildFullProfile,
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
      kolName: kolHumanDisplayName(item),
      staff,
      apiToken,
      onClose: () => setShareOpen(false),
    })
  );
}
