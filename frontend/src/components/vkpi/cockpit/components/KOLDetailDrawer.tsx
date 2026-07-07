// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Share2, Sparkles } from "lucide-react";
import { KOLVideoAnalysisPanel } from "./KOLVideoAnalysisPanel";
import { ShareKolModal } from "../../shared/ShareKolModal";
import { enqueueAllKolVideos, enqueueKolProfileCrawl, enqueueVideoAnalysis, getKolPoolContentFit, getKolPoolDimensions11, getKolPoolLlmDeepAnalysis, promoteKolPoolToMain, refreshAudienceStats } from "../../../../services/vkpi/kolPool-api";
import { getKolMemory } from "../../../../services/vkpi/kolMemory-api";
import { KOLDrawerOutreachSection } from "./KOLDrawerOutreachSection";
import { runSkill, type SkillRunResult } from "../../../../services/vkpi/skills-api";
import { candidateKindGroup } from "../lib/candidateKind";
import { GoaffproLinkSection } from "../../shared/GoaffproLinkSection";
import {
  asArray,
  detailBundleAnalysisItems,
  detailBundleAnalysisSummary,
  dimensions11RadarDims,
  evidenceIdOf,
  isImageEvidence,
  recordOr,
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

// 【C1 Tab 化】抽屉内容区四 tab:按「用户看数据的心智」把既有 section 分组渲染(零改各 section 内部)。
// 概览=这人是谁/分数为何/内容速览;深度分析=LLM/视频深析产物;受众=粉丝画像;合作=推进合作的动作面。
// 头部身份卡与底部粘性行动条在 tab 结构之外,全 tab 常驻。tab 选择记忆到 localStorage(跨 KOL/会话)。
const DRAWER_TABS: Array<{ key: string; label: string }> = [
  { key: "overview", label: "概览" },
  { key: "deep", label: "深度分析" },
  { key: "audience", label: "受众" },
  { key: "coop", label: "合作" },
];
const DRAWER_TAB_STORAGE_KEY = "vkpi:drawer-active-tab";
function readStoredDrawerTab(): string {
  try {
    const raw = window.localStorage.getItem(DRAWER_TAB_STORAGE_KEY);
    if (raw && DRAWER_TABS.some((tab) => tab.key === raw)) return raw;
  } catch { /* 隐私模式读失败按默认「概览」 */ }
  return "overview";
}

export function KOLDetailDrawer({ item, detailBundle = null, apiToken = "", detailLoading = false, detailError = "", onClose, inMyList, onToggleMyList, onContact, staff = [], onReloadDetail }: any) {
  // P-GROUP-7 共享 KOL 池:把这条 My KOL(item.id = kol_pool_id)显式共享给成员(只读授予)。
  const [shareOpen, setShareOpen] = React.useState(false);
  // 【M3/M5】观看者上下文:共享来源(来自谁的共享)+ active 认领(本人/管理层可释放)。
  // 开抽屉只读拉取(动态 import 保持 mock seam 不变),失败静默 → 不渲染徽标(graceful absence)。
  const [viewerCtx, setViewerCtx] = React.useState<any>(null);
  const [releaseBusy, setReleaseBusy] = React.useState(false);
  const [releaseMsg, setReleaseMsg] = React.useState("");
  React.useEffect(() => {
    setViewerCtx(null);
    setReleaseBusy(false);
    setReleaseMsg("");
    if (!apiToken || !item?.id) return;
    let cancelled = false;
    void import("../../../../services/vkpi/kol-api")
      .then(({ getMyKolViewerContext }) => getMyKolViewerContext(apiToken, item.id))
      .then((payload: any) => {
        if (!cancelled) setViewerCtx(payload && typeof payload === "object" ? payload : null);
      })
      .catch(() => { if (!cancelled) setViewerCtx(null); });
    return () => { cancelled = true; };
  }, [apiToken, item?.id]);
  // 【M3】释放认领:确认 → 乐观摘掉认领条 → POST /claims/{id}/release;失败回滚 + 显示原因。
  // 权限由后端把关(认领人本人或管理层),按钮只在 can_release=true 时出现。
  const handleReleaseClaim = React.useCallback(() => {
    const claim = viewerCtx?.claim;
    const claimId = claim?.id;
    if (!apiToken || !claimId || releaseBusy) return;
    const kolLabel = String(item?.display_name || item?.handle || "该 KOL");
    if (!window.confirm(`确认释放对「${kolLabel}」的认领?释放后该 KOL 可被其他成员认领。`)) return;
    setReleaseBusy(true);
    setReleaseMsg("");
    const snapshot = viewerCtx;
    setViewerCtx((prev: any) => (prev ? { ...prev, claim: null } : prev));
    void import("../../../../services/vkpi/kol-api")
      .then(({ releaseKolClaim }) => releaseKolClaim(apiToken, String(claimId)))
      .then(() => setReleaseMsg("已释放认领"))
      .catch((error: any) => {
        setViewerCtx(snapshot);
        setReleaseMsg("释放失败:" + String(error?.message || "请重试").slice(0, 80));
      })
      .finally(() => setReleaseBusy(false));
  }, [apiToken, viewerCtx, releaseBusy, item?.display_name, item?.handle]);
  // 【C1】当前 tab:惰性读 localStorage(非法值回落「概览」);切换时写回记忆。
  const [activeTab, setActiveTab] = React.useState<string>(() => readStoredDrawerTab());
  const handleSelectTab = React.useCallback((key: string) => {
    setActiveTab(key);
    try {
      window.localStorage.setItem(DRAWER_TAB_STORAGE_KEY, key);
    } catch { /* 写失败不阻塞切换,本次会话内 state 仍生效 */ }
  }, []);
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
  // 新发现 KOL 档案瘦/无视频证据 → 打开抽屉自动补:入队 profile 深爬(后端幂等,
  // 已有 evidence/活跃 job 不重复烧 Apify),入队成功后 30s×10 静默重拉 detail_bundle 等档案落地。
  const profileCrawlRef = React.useRef<any>({ firedId: null, timer: null });
  // 【A2/A3】抓取态(手动按钮 + 自动入队共用):idle/loading(入队请求中)/crawling(已入队,轮询等落地)/done/error。
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
  // 【A1/A2 共用】入队 profile 深爬。auto=true 为抽屉自动补档路径(失败静默,不打扰);
  // 手动路径把 already_queued/already_has_evidence/no_profile_url 等全部翻成人话反馈。
  const startProfileCrawl = React.useCallback((auto: boolean = false) => {
    const id = item?.id;
    if (!apiToken || !id) return;
    setProfileCrawlState({ status: "loading", message: "" });
    void enqueueKolProfileCrawl(apiToken, id)
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
        } else if (auto) {
          // 自动补档路径:not_found/no_profile_url 等静默归位,原有手动按钮仍可用。
          setProfileCrawlState({ status: "idle", message: "" });
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
        if (auto) {
          setProfileCrawlState({ status: "idle", message: "" });
          return;
        }
        setProfileCrawlState({ status: "error", message: error?.message ? String(error.message) : "抓取入队失败" });
      });
  }, [apiToken, item?.id, onReloadDetail, startCrawlPolling]);
  // 【A1 触发扩围】自动补档触发条件从「档案瘦(帖数/均播全空)」扩成:档案瘦 或
  // 「detail_bundle 已到且该 KOL 没有任何视频证据」(item.video_evidence 空 + video_analysis.items 空)。
  // detailBundle 异步到达,故必须进依赖;firedId 防重不变;enqueueKolProfileCrawl 后端幂等,多触发安全。
  React.useEffect(() => {
    const id = item?.id;
    if (!apiToken || !id || profileCrawlRef.current.firedId === id) return;
    const thin = !Number(item?.posts_count || 0) && !Number(item?.avg_views || 0);
    const bundle = recordOr(detailBundle);
    const bundleNoEvidence = bundle.status === "ready"
      && asArray(recordOr(bundle.item).video_evidence).length === 0
      && detailBundleAnalysisItems(bundle).length === 0;
    if (!thin && !bundleNoEvidence) return;
    profileCrawlRef.current.firedId = id;
    startProfileCrawl(true);
  }, [apiToken, item?.id, item?.posts_count, item?.avg_views, detailBundle, startProfileCrawl]);
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
    setAllVideosState({ status: "loading", message: "正在把该 KOL 的全部视频证据加入深析队列…" });
    void enqueueAllKolVideos(apiToken, item.id)
      .then((payload: any) => {
        const status = String(payload?.status || "");
        if (status === "no_evidence") {
          // 【A2】后端确认无证据 → 自动转为抓取(account_deep),不让用户对着死路按钮干瞪眼。
          setAllVideosState({ status: "no_evidence", message: "该 KOL 暂无视频证据 — 已自动转为抓取(account_deep 模式)" });
          startProfileCrawl(false);
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
  e(motion.div, {
    initial: { x: "100%" }, animate: { x: 0 }, exit: { x: "100%" },
    transition: { type: "spring", damping: 28, stiffness: 240 },
    "aria-label": "KOL Pool 详情",
    className: "fixed top-0 right-0 h-full w-[520px] bg-[#0a1020] border-l border-white/[0.08] shadow-2xl z-50 flex flex-col"
  },
    // ─── Header ───
    e(KOLDrawerHeader, { item, devices, detailLoading, detailError, onClose }),

    // ── 【M3/M5】观看者上下文条:来自谁的共享 + 认领状态/释放(有数据才渲染,全 tab 常驻)──
    (viewerCtx?.share_origin || viewerCtx?.claim || releaseMsg) && e("div", {
      className: "flex flex-wrap items-center gap-1.5 border-b border-white/[0.06] px-5 py-1.5",
    },
      viewerCtx?.share_origin && e("span", {
        className: "rounded border border-purple-400/30 bg-purple-400/[0.08] px-1.5 py-0.5 text-[9px] font-medium text-purple-200",
        title: "该 KOL 经共享池(P-GROUP-7)共享给你 · 只读可见,非本人收藏"
          + (viewerCtx.share_origin.created_at ? " · " + String(viewerCtx.share_origin.created_at).slice(0, 10) : ""),
      }, "来自 " + (viewerCtx.share_origin.shared_by_name || "未知成员") + " 的共享"),
      viewerCtx?.claim && e("span", {
        className: "rounded border border-amber-400/25 bg-amber-400/[0.06] px-1.5 py-0.5 text-[9px] text-amber-200",
        title: "active 认领(vkpi_kol_claims)"
          + (viewerCtx.claim.expires_at ? " · 到期 " + String(viewerCtx.claim.expires_at).slice(0, 10) : ""),
      }, "已认领 · " + (viewerCtx.claim.staff_name || ("成员 " + (viewerCtx.claim.staff_id ?? "—")))),
      viewerCtx?.claim?.can_release && e("button", {
        type: "button",
        disabled: releaseBusy,
        onClick: handleReleaseClaim,
        className: "rounded border border-amber-400/40 px-1.5 py-0.5 text-[9px] font-medium text-amber-300 transition-colors hover:bg-amber-400/[0.10] disabled:opacity-50",
        title: "释放认领:取消认领回池,他人可再认领(认领人本人或管理层可操作)",
      }, releaseBusy ? "释放中…" : "释放"),
      releaseMsg && e("span", {
        className: "text-[9px] " + (releaseMsg.startsWith("释放失败") ? "text-rose-300" : "text-emerald-300"),
      }, releaseMsg)
    ),

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
        e(KOLDrawerContactAndVideos, { item, representativeVideos, onOpenVideo: setActiveRepresentativeVideo }),
        // ── 推荐动作/文本区块: Viltrox 适配判断 / 推荐产品线 / 风险点 / 品牌合作历史 ──
        e(KOLDrawerTextSections, { item, recommendedProductLines, potentialConcerns, brandCollaborations, competitorCollabs, foldDefaultOpen }),
      ),

      // ══ Tab「深度分析」:视频深析产物 + LLM 判断 + 档案 + 内容契合 ══
      activeTab === "deep" && e(React.Fragment, null,
        // 【A3】抓取中把轮询态透传给深析结果区标题旁的小徽标(复用组件内已有轮询态,零全局依赖)。
        e(KOLVideoAnalysisPanel, { apiToken, videos: videoAnalysisVideos, preloadedBundles: preloadedVideoAnalysisBundles, summary: videoAnalysisSummary, crawling: crawlBusy }),
        // ── 全视频跑:该 KOL 全部视频证据各入队一条 final_v1 → 发完综合评估 ──
        // 【A2】无 evidence 时本按钮变「一键抓取」:点击入队 profile 深爬(account_deep 级联),
        // 按钮态「抓取中…」+ 30s×10 轮询 onReloadDetail 等证据落地;已入队/已有证据都给人话反馈。
        apiToken && item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
          e("button", {
            type: "button",
            disabled: allVideosBusy || (noEvidenceKnown && crawlBusy),
            onClick: noEvidenceKnown ? () => startProfileCrawl(false) : handleEnqueueAllVideos,
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
        e(SimilarVideosPanel, { apiToken, evidenceId: primaryVideoEvidenceId }),
        // ── 第4轮 商业档案:预测战绩(分位数区间+置信度)/ 报价卡(估价+录入) ──
        e(ForecastPanel, { apiToken, kolPoolId: item?.id }),
        e(RateCardPanel, { apiToken, kolPoolId: item?.id }),
        // 地基B:内容契合深析(content_fit_v1)——基于视频画面/故事 + 评论的适配判断(胜过粉丝数)。
        e(KOLDrawerContentFit, { apiToken, item, contentFit, contentFitBusy, contentFitError, onAnalyze: handleContentFitAnalyze }),
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
        // ── 长期记忆(W3)· 显式独立于 V6 Fit · 不影响排序 ──
        // 红线:本区块纯渲染聚合记忆,绝不渲染任何 viltrox/v6_fit 数值。
        e(KOLDrawerMemorySection, { kolMemory }),
        // ── N2 跑 Skill:brief_generate → 合作 brief 草案(默认走规则模板,不烧 LLM;红线零触 fit)──
        apiToken && item?.id && e(KOLDrawerBriefSkill, {
          result: briefResult, busy: briefBusy, error: briefError, onRun: handleRunBriefSkill,
        }),
        // ── D2 生成追踪链(GOAFFPRO):一键给该 KOL 建 affiliate + 追踪链 + 优惠码(KOL 零注册)──
        e(GoaffproLinkSection, { apiToken, kolPoolId: item?.id }),
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
        // ── 第2轮 查看完整档案:跳八层组装页(id 走 sessionStorage,CockpitApp 监听切板块)──
        item?.id && e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
          e("button", {
            type: "button",
            onClick: () => {
              window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.id));
              window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile"));
            },
            className: "flex w-full items-center justify-center gap-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/[0.06] px-3 py-2 text-[11px] font-medium text-cyan-200 transition-colors hover:bg-cyan-400/[0.12]",
          },
            e(Sparkles, { size: 12 }),
            "查看完整档案"
          )
        ),
      ),
    ),
    
    // ─── Footer actions ───
    e(KOLDrawerFooter, {
      item, inMyList, onToggleMyList, onContact, onPromote, promoteMsg,
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
