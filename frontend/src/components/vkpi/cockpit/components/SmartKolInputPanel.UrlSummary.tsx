// URL 深析展示子组件：VideoCreatorCard(创作者账号卡)、VideoMediaSummary(播放/封面降级)、
// VideoSceneAnalysis(URL 视频内联深析,纯读 final_v1/QA 缓存)、UrlSummary(URL 结果卡容器)。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
// Sections 仍 re-export UrlSummary,容器调用面不变。
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  CalendarDays,
  Clock3,
  Database,
  ExternalLink,
  Eye,
  Heart,
  Loader2,
  MessageCircle,
  UserPlus,
  Video,
} from "lucide-react";

import { type VkpiKolUrlDeepCrawlResponse } from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import {
  deepCrawlKolUrl,
  enqueueAllKolVideos,
  getKolPoolAccountDossier,
  getKolPoolDetailBundle,
  getKolPoolItem,
  getKolRecommendationCard,
  type VkpiKolPoolDetailBundleResponse,
} from "../../../../services/vkpi/kolPool-api";
import { containsOpaqueKolChannelId, isOpaqueKolChannelId, kolHumanDisplayName, kolHumanPublicHandle } from "../lib/kolIdentity";

import {
  actionDescription,
  asRecord,
  cleanText,
  display,
  durationLabel,
  numberLabel,
  profileStatusCopyFor,
  profileUiState,
  type ProfileUiState,
  urlTypeLabel,
  youtubeEmbedUrl,
  type Row,
} from "./SmartKolInputPanel.helpers";
import { ProfileInfoCard } from "./SmartKolInputPanel.Sections";
import { VideoCreatorCard } from "./SmartKolInputPanel.UrlSummary.CreatorCard";
import { humanizeLlmReason } from "../llmReasonCopy";
import { VideoSceneAnalysis } from "./SmartKolInputPanel.UrlSummary.VideoAnalysis";
import {
  CnPlatformVideoNotice,
  dateLabel,
  firstSafeCachedVideoUrl,
  firstSafeHttpUrl,
  mergePublicProfiles,
  publicFieldRows,
  publicProfileFields,
  safeCachedVideoUrl,
  safeHttpUrl,
  secondsLabel,
  VideoPoster,
} from "./SmartKolInputPanel.UrlSummary.shared";

const VideoCommentSamples = lazy(() => import("./SmartKolInputPanel.UrlSummary.Comments"));
const AccountUrlInlineOverview = lazy(() =>
  import("./SmartKolInputPanel.UrlSummary.AccountOverview").then((module) => ({
    default: module.AccountUrlInlineOverview,
  })),
);

function VideoMediaSummary({
  platform,
  metadata,
  youtubeVideoId,
  cachedVideoUrl,
  sourceUrl,
}: {
  platform: string;
  metadata: Row;
  youtubeVideoId: string;
  cachedVideoUrl: string;
  sourceUrl: string;
}) {
  type CachePlaybackState = "idle" | "loading" | "ready" | "error";
  const title = cleanText(metadata.title || metadata.description) || "视频详情";
  const description = cleanText(metadata.description);
  const posterSource = firstSafeHttpUrl(metadata.thumbnail_url, Array.isArray(metadata.image_urls) ? metadata.image_urls[0] : "");
  const poster = proxiedImageUrl(posterSource);
  const safeVideoUrl = safeCachedVideoUrl(cachedVideoUrl);
  const safeSourceUrl = safeHttpUrl(sourceUrl);
  // 缓存 URL 存在只证明“有地址”，不证明浏览器真的能播。状态与 URL 绑定，
  // A→B 切换的首帧不会沿用 A 的 ready；只有 loadedmetadata/canplay 后才标记可播。
  const [storedPlayback, setStoredPlayback] = useState<{ url: string; state: CachePlaybackState }>({
    url: "",
    state: "idle",
  });
  const cachePlaybackState: CachePlaybackState = safeVideoUrl
    ? storedPlayback.url === safeVideoUrl ? storedPlayback.state : "loading"
    : "idle";
  useEffect(() => {
    setStoredPlayback({ url: safeVideoUrl, state: safeVideoUrl ? "loading" : "idle" });
  }, [safeVideoUrl]);
  const markPlayback = (state: CachePlaybackState) => {
    if (safeVideoUrl) setStoredPlayback({ url: safeVideoUrl, state });
  };
  const duration = secondsLabel(metadata.duration_seconds);
  const published = dateLabel(metadata.publish_date || metadata.posted_at);
  const views = numberLabel(metadata.view_count ?? metadata.play_count ?? metadata.views);
  const likes = numberLabel(metadata.like_count ?? metadata.likes);
  const comments = numberLabel(metadata.comment_count ?? metadata.comments);
  const youtubePlayable = platform === "youtube" && Boolean(youtubeVideoId);
  const r2Candidate = platform !== "youtube" && Boolean(safeVideoUrl);
  const r2Playable = r2Candidate && cachePlaybackState === "ready";
  const r2Failed = r2Candidate && cachePlaybackState === "error";
  const playable = youtubePlayable || r2Playable;
  const sourceLabel = platform === "instagram" ? "Instagram 原帖" : platform === "tiktok" ? "TikTok 原帖" : platform === "youtube" ? "YouTube 原视频" : "原帖";

  return (
    <div className="mt-2 overflow-hidden rounded-lg border border-white/[0.08] bg-slate-950/35" data-testid="video-url-summary">
      <div className="grid gap-0 lg:grid-cols-[minmax(260px,0.9fr)_minmax(320px,1.1fr)]">
        <div className="border-b border-white/[0.07] bg-black/45 lg:border-b-0 lg:border-r">
          <div className="relative w-full" style={{ aspectRatio: "16 / 9" }}>
            {youtubePlayable ? (
              <iframe
                src={youtubeEmbedUrl(youtubeVideoId)}
                title={title}
                className="absolute inset-0 h-full w-full"
                allow="autoplay; encrypted-media; picture-in-picture"
                referrerPolicy="strict-origin-when-cross-origin"
                allowFullScreen
              />
            ) : r2Candidate && !r2Failed ? (
              <video
                src={safeVideoUrl}
                poster={poster || undefined}
                controls
                playsInline
                preload="metadata"
                className="absolute inset-0 h-full w-full bg-black object-contain"
                onLoadedMetadata={() => markPlayback("ready")}
                onCanPlay={() => markPlayback("ready")}
                onError={() => markPlayback("error")}
                data-testid="cached-video-player"
              />
            ) : (
              <VideoPoster source={posterSource} title={title} />
            )}
          </div>
          {!playable ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-300/15 bg-amber-400/[0.07] px-3 py-2 text-[10px] text-amber-100">
              <span className="inline-flex items-center gap-1.5"><Clock3 size={11} /> {
                r2Failed
                  ? "缓存视频加载失败，请打开原帖查看"
                  : r2Candidate
                    ? "缓存地址已发现，正在验证浏览器播放"
                    : "视频缓存未就绪，暂时不可播放"
              }</span>
              {safeSourceUrl ? (
                <a href={safeSourceUrl} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-cyan-200 hover:text-cyan-100 hover:underline">
                  打开 {sourceLabel} <ExternalLink size={10} />
                </a>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="flex min-w-0 flex-col p-3">
          <div className="flex flex-wrap items-center gap-1.5 text-[9px]">
            <span className="rounded border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 uppercase tracking-wide text-slate-400">{platform || "video"}</span>
            {r2Playable ? <span className="rounded border border-emerald-300/20 bg-emerald-400/[0.08] px-1.5 py-0.5 text-emerald-100">缓存视频可播放</span> : null}
            {r2Candidate && cachePlaybackState === "loading" ? <span className="rounded border border-cyan-300/20 bg-cyan-400/[0.08] px-1.5 py-0.5 text-cyan-100">缓存视频加载中</span> : null}
            {r2Failed ? <span className="rounded border border-rose-300/20 bg-rose-400/[0.08] px-1.5 py-0.5 text-rose-100">缓存视频加载失败</span> : null}
            {youtubePlayable ? <span className="rounded border border-emerald-300/20 bg-emerald-400/[0.08] px-1.5 py-0.5 text-emerald-100">YouTube 播放源已就绪</span> : null}
          </div>
          <h3 className="mt-2 line-clamp-2 text-[13px] font-semibold leading-snug text-slate-100">{title}</h3>
          {description && description !== title ? (
            <p className="mt-1.5 line-clamp-3 text-[10.5px] leading-relaxed text-slate-400">{description}</p>
          ) : null}
          <div className="mt-3 grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            {views ? <span className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-[10px] text-slate-300"><Eye size={11} className="text-sky-300" /> {views} 播放</span> : null}
            {likes ? <span className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-[10px] text-slate-300"><Heart size={11} className="text-rose-300" /> {likes} 点赞</span> : null}
            {comments ? <span className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-[10px] text-slate-300"><MessageCircle size={11} className="text-violet-300" /> {comments} 评论</span> : null}
            {duration ? <span className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-[10px] text-slate-300"><Clock3 size={11} className="text-cyan-300" /> {duration}</span> : null}
            {published ? <span className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-[10px] text-slate-300"><CalendarDays size={11} className="text-amber-300" /> {published}</span> : null}
          </div>
          {safeSourceUrl ? (
            <a href={safeSourceUrl} target="_blank" rel="noreferrer noopener" className="mt-auto inline-flex items-center gap-1 self-start pt-3 text-[10px] font-medium text-cyan-300/85 hover:text-cyan-100 hover:underline">
              查看原帖 <ExternalLink size={10} />
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function UrlSummary({
  result,
  apiToken,
  canExecute,
  isExecuting,
  onExecute,
  onLocalEvaluation,
  onOpenProfile,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
  apiToken: string;
  canExecute: boolean;
  isExecuting: boolean;
  onExecute: () => void;
  onLocalEvaluation?: () => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
}) {
  const [polledVideo, setPolledVideo] = useState<{ evidenceId: string; sourceUrl: string; url: string } | null>(null);
  const [hydratedProfile, setHydratedProfile] = useState<{
    kolId: number;
    data: Row;
    item: Row;
    freshness: Row;
    recommendation: Row;
  } | null>(null);
  const resultRow = asRecord(result);
  const isVideo = result.url_type === "video";
  const profileFlow = asRecord(result.profile_flow);
  const videoFlow = asRecord(result.video_flow);
  const videoResolution = asRecord(videoFlow.resolution_progress || resultRow.video_url_resolution);
  const videoResolutionSteps = Array.isArray(videoResolution.steps)
    ? videoResolution.steps.map((step) => asRecord(step)).slice(0, 4)
    : [];
  const profileExecute = asRecord(resultRow.profile_execute);
  const nestedVideoProfileFlow = asRecord(videoFlow.profile_flow);
  const profileExecuteData = asRecord(profileExecute.profile_data);
  const nestedVideoProfileData = asRecord(nestedVideoProfileFlow.profile_data);
  const flowProfileData = asRecord(profileFlow.profile_data);
  const matchedKolId = Number(result.matched_kol_pool_id || profileFlow.kol_pool_id || videoFlow.kol_pool_id) || 0;
  const searchSession = asRecord(result.search_session);
  // 历史会话回放时 profile_execute 是 worker 后写的最新状态，应优先于初始
  // profile_flow=queued。只有明确 ready 才可宣称“已入库”。
  const rawProfileStatus = cleanText(
    profileExecute.status
    || profileFlow.job_status
    || profileFlow.status
    || searchSession.item_status
    || resultRow.status,
  );
  const profileState = profileUiState(rawProfileStatus, !isVideo && isExecuting);
  const repAnalysis = {
    ...asRecord(profileFlow.representative_video_analysis),
    ...asRecord(profileExecute.representative_video_analysis),
    ...asRecord(resultRow.representative_video_analysis),
  };
  const repAiAnalysis = asRecord(repAnalysis.ai_analysis);
  const repStatus = cleanText(repAnalysis.job_status || repAnalysis.status).toLowerCase();
  const repQueued = Math.max(0, Number(repAnalysis.queued ?? 0) || 0);
  const repReady = Math.max(0, Number(repAnalysis.ready ?? repAnalysis.completed ?? 0) || 0);
  const repFailed = Math.max(0, Number(repAnalysis.failed ?? 0) || 0);
  const accountAiDisabled = Boolean(
    cleanText(repAiAnalysis.state).toLowerCase() === "not_requested"
    || cleanText(repAiAnalysis.reason).toLowerCase() === "ai_disabled"
    || repStatus === "ai_disabled"
  );
  const accountPipelineActive = !isVideo && !accountAiDisabled && (
    ["queued", "running"].includes(profileState)
    || ["queued", "already_queued", "pending", "running", "processing", "retrying"].includes(repStatus)
    || (repQueued > repReady + repFailed && !["ready", "done", "completed"].includes(repStatus))
  );
  // 同一 KOL id 下状态/代表视频进度变化也要刷新，不再只依赖 matchedKolId。
  const accountRefreshKey = [
    rawProfileStatus,
    cleanText(profileFlow.job_id || profileExecute.run_id),
    repStatus,
    repQueued,
    repReady,
    repFailed,
    cleanText(searchSession.updated_at || searchSession.item_status),
  ].join(":");
  const [accountBundleState, setAccountBundleState] = useState<{
    kolId: number;
    status: "idle" | "loading" | "ready" | "error";
    bundle: VkpiKolPoolDetailBundleResponse | null;
    dossier: Row;
    error: string;
  }>({ kolId: 0, status: "idle", bundle: null, dossier: {}, error: "" });
  const accountPollDeadlineRef = useRef<{ identity: string; startedAt: number }>({ identity: "", startedAt: 0 });
  const accountPollIdentity = [
    matchedKolId,
    cleanText(
      searchSession.item_id
      || searchSession.id
      || searchSession.search_session_id
      || profileFlow.search_session_item_id
      || resultRow.search_session_item_id
      || result.url?.normalized
      || result.url?.input,
    ),
  ].join(":");
  // useEffect 清空发生在 commit 后；渲染期按 kolId 过滤，历史 A→B 的首帧也不会消费 A 的补读资料。
  const currentHydratedProfile = hydratedProfile?.kolId === matchedKolId ? hydratedProfile.data : {};
  const currentHydratedItem = hydratedProfile?.kolId === matchedKolId ? hydratedProfile.item : {};
  const currentFreshness = hydratedProfile?.kolId === matchedKolId ? hydratedProfile.freshness : {};
  const currentRecommendation = hydratedProfile?.kolId === matchedKolId ? hydratedProfile.recommendation : {};
  useEffect(() => {
    let cancelled = false;
    setHydratedProfile(null);
    if (!apiToken || !matchedKolId) return undefined;
    // 只读现有池档案，且 refresh_if_stale=false：补足历史会话 compact flow 丢掉的头像/粉丝/帖数/简介，
    // 不触发 provider，不要求用户先点“全部字段”或打开抽屉。
    Promise.all([
      getKolPoolItem(apiToken, matchedKolId, false)
        .then((response) => response)
        .catch(() => null),
      // 推荐卡是现有纯读投影:data_grade 明确只是数据完整度档(非 Fit),
      // why/signals 来自已入库 provenance,不会因为打开 URL 触发模型或写评分。
      getKolRecommendationCard(apiToken, matchedKolId)
        .then((response) => asRecord(response))
        .catch(() => ({})),
    ]).then(([response, recommendation]) => {
      if (cancelled || (!response && !Object.keys(recommendation).length)) return;
      setHydratedProfile({
        kolId: matchedKolId,
        data: publicProfileFields(response?.item),
        item: asRecord(response?.item),
        freshness: asRecord(response?.freshness),
        recommendation,
      });
    });
    return () => { cancelled = true; };
  }, [apiToken, matchedKolId]);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    if (!apiToken || !matchedKolId || isVideo) return undefined;
    if (accountPollDeadlineRef.current.identity !== accountPollIdentity) {
      accountPollDeadlineRef.current = { identity: accountPollIdentity, startedAt: Date.now() };
    }
    const startedAt = accountPollDeadlineRef.current.startedAt;
    setAccountBundleState((current) => current.kolId === matchedKolId
      ? { ...current, status: "loading", error: "" }
      : { kolId: matchedKolId, status: "loading", bundle: null, dossier: {}, error: "" });
    // 详情 bundle 是现有的纯读聚合端点:provider_calls=false / llm_calls=false / write_db=false。
    // 两条现有纯读投影并发:detail-bundle 给 final_v1 内容，dossier 给全量覆盖计数/结论。
    // 首屏最多渲染 6 条视频，即使库内更多也不放大 DOM。队列活跃时最长重读
    // 30 分钟：前 2 分钟每 6s，之后每 10s；切换状态或 KOL 会 cleanup，旧请求不能覆盖新结果。
    const load = () => {
      Promise.all([
        getKolPoolDetailBundle(apiToken, matchedKolId, { videoLimit: 10, llmLimit: 6 })
          .then((bundle) => ({ bundle, error: "" }))
          .catch((error: unknown) => ({ bundle: null, error: error instanceof Error ? cleanText(error.message).slice(0, 120) : "" })),
        getKolPoolAccountDossier(apiToken, matchedKolId)
          .then((dossier) => ({ dossier: asRecord(dossier), error: "" }))
          .catch((error: unknown) => ({ dossier: {}, error: error instanceof Error ? cleanText(error.message).slice(0, 120) : "" })),
      ]).then(([detailResult, dossierResult]) => {
        if (cancelled) return;
        const hasDetail = Boolean(detailResult.bundle);
        const hasDossier = Object.keys(dossierResult.dossier).length > 0;
        const errors = [detailResult.error, dossierResult.error].filter(Boolean).join("；");
        setAccountBundleState({
          kolId: matchedKolId,
          status: hasDetail || hasDossier ? "ready" : "error",
          bundle: detailResult.bundle,
          dossier: dossierResult.dossier,
          error: hasDetail || hasDossier ? "" : errors,
        });
        const elapsed = Date.now() - startedAt;
        if (accountPipelineActive && elapsed < 30 * 60_000) {
          timer = window.setTimeout(load, elapsed < 120_000 ? 6000 : 10000);
        }
      });
    };
    load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [accountPipelineActive, accountPollIdentity, accountRefreshKey, apiToken, isVideo, matchedKolId]);
  const currentAccountState = accountBundleState.kolId === matchedKolId
    ? accountBundleState
    : { kolId: matchedKolId, status: "loading" as const, bundle: null, dossier: {}, error: "" };
  const currentAccountBundle = !isVideo ? currentAccountState.bundle : null;
  const currentAccountDossier = !isVideo ? currentAccountState.dossier : {};
  const dossierProfile = asRecord(currentAccountDossier.profile);
  const dossierVideos = Array.isArray(currentAccountDossier.videos)
    ? currentAccountDossier.videos.map((video) => asRecord(video))
    : [];
  const dossierChannelName = cleanText(dossierVideos.find((video) => cleanText(video.channel_name))?.channel_name);
  const currentAccountItem = {
    ...currentHydratedItem,
    ...dossierProfile,
    ...asRecord(currentAccountBundle?.item),
    ...(dossierChannelName ? { display_name: dossierChannelName } : {}),
  };
  const creator = mergePublicProfiles(
    {
      platform: result.platform,
      handle: result.handle,
      channel_id: result.channel_id,
      // 视频原帖不是账号主页；只有 profile URL 才可把输入链接用作 profile_url 兜底。
      profile_url: result.url_type === "video" ? "" : firstSafeHttpUrl(result.url?.normalized, result.url?.input),
    },
    videoFlow.creator_identity,
    result.creator_identity,
    currentHydratedProfile,
    profileExecuteData,
    nestedVideoProfileData,
    flowProfileData,
    currentAccountItem,
  );
  const metadata = {
    ...asRecord(videoFlow.video_metadata),
    ...asRecord(result.video_metadata),
  };
  const analysis = asRecord(videoFlow.analysis);
  const videoAiAnalysis = {
    ...asRecord(asRecord(videoFlow.enqueue_result).ai_analysis),
    ...asRecord(videoFlow.ai_analysis),
  };
  const jobLastError = cleanText(
    videoFlow.job_last_error || videoFlow.error || videoFlow.reason_detail || videoFlow.reason ||
    videoAiAnalysis.reason || videoAiAnalysis.gate_reason ||
    profileFlow.job_last_error || profileFlow.error || profileFlow.reason_detail || profileFlow.reason,
  );
  const jobFailureCopy = humanizeLlmReason(jobLastError, "后台分析任务未完成，请检查模型授权与预算状态。");
  const jobStatus = cleanText(videoFlow.job_status || videoAiAnalysis.state || profileFlow.job_status || videoFlow.status || profileFlow.status);
  const flowStatus = cleanText(videoFlow.status || profileFlow.status || (result.search_session ? asRecord(result.search_session).item_status : ""));
  const latency = durationLabel(analysis.latency_ms);
  const platform = cleanText(result.platform).toLowerCase();
  // 顶层兜底:execute 响应把 cached_video_url/evidence_id 摊平到 result 顶层(不在嵌套 video_flow 下),
  // 旧码只读 videoFlow.* → 取空 → 播放器/分镜静默不渲染。两处都加 result.* 兜底。
  const rawEvidenceId = videoFlow.evidence_id ?? resultRow.evidence_id;
  const effectiveEvidenceId = Number(rawEvidenceId) > 0 ? String(rawEvidenceId).trim() : "";
  const youtubeVideoId = cleanText(result.video_id || videoFlow.video_id);
  const sourceVideoUrl = firstSafeHttpUrl(metadata.content_url, result.url?.normalized, result.url?.input);
  const currentPolledVideoUrl = polledVideo?.evidenceId === effectiveEvidenceId && polledVideo.sourceUrl === sourceVideoUrl
    ? polledVideo.url
    : "";
  const cachedVideoUrl = firstSafeCachedVideoUrl(videoFlow.cached_video_url, resultRow.cached_video_url, currentPolledVideoUrl);
  const handlePolledVideoUrl = useCallback((value: string) => {
    const safe = safeCachedVideoUrl(value);
    if (safe) setPolledVideo({ evidenceId: effectiveEvidenceId, sourceUrl: sourceVideoUrl, url: safe });
  }, [effectiveEvidenceId, sourceVideoUrl]);
  // 图片轮播:IG /p/ 按 URL 模式被当 video,但实际是图文/多图轮播(后端识别分流标 media_kind=image)。
  // 此时不放视频播放器/分镜,直接展示图片轮转。image_urls 由 _public_video_metadata 透传。
  const imageUrls = Array.isArray(metadata.image_urls)
    ? (metadata.image_urls as unknown[]).map((u) => safeHttpUrl(u)).filter(Boolean)
    : [];
  const isImageCarousel = cleanText(metadata.media_kind) === "image" && imageUrls.length > 0;
  const profileOperation = cleanText(profileFlow.operation);
  const videoOperation = cleanText(videoFlow.operation);
  const operation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : videoOperation || profileOperation;
  const knownCreator = Boolean(result.in_pool || operation === "existing_creator_video_analysis");
  const creatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(creator.handle || creator.channel_id || creator.profile_url || result.handle || result.channel_id),
  );
  const tiktokRisk = isVideo && platform === "tiktok";
  const analysisDisabled = Boolean(
    isVideo && (
      jobStatus.toLowerCase() === "not_requested"
      || cleanText(videoAiAnalysis.reason).toLowerCase() === "ai_disabled"
      || /ai_disabled|readiness_not_production_ready|model_binding_blocked|operator_disabled/i.test(jobLastError)
    )
  );
  const analysisFailed = Boolean(
    isVideo
    && !analysisDisabled
    && ["failed", "blocked", "error", "cancelled", "canceled"].includes(jobStatus.toLowerCase())
  );
  const localEvaluationEligible = Boolean(
    import.meta.env.DEV
    && isVideo
    && (analysisFailed || analysisDisabled)
    && /model_binding_blocked|readiness_not_production_ready|budget_guard_blocked/i.test(jobLastError)
    && onLocalEvaluation,
  );
  const retryableFailure = analysisFailed;
  // 官方自有账号视频:后台已识别创作者是公司官方渠道,按设计不建档、不深析(仅展示基础数据)。
  // 属正常终态:计入 videoReady,绝不能落进「深析排队中」的假等待。
  const officialChannelVideo = Boolean(
    isVideo && (
      cleanText(videoAiAnalysis.reason) === "official_channel_video"
      || cleanText(videoFlow.status) === "official_channel_video"
      || flowStatus === "official_channel_video"
    ),
  );
  // 中国平台视频(bilibili/抖音/小红书):仅做内容分析,不建人选档案(设计定案)。
  // 正常终态:计入 videoReady,不落假排队;分析摘要直接在横幅里展示。
  const cnPlatformVideo = Boolean(
    isVideo && !officialChannelVideo && (
      videoFlow.cn_platform_video === true
      || ["cn_platform_video", "cn_platform_video_planned"].includes(cleanText(videoFlow.status))
      || ["cn_platform_video", "cn_platform_video_planned"].includes(flowStatus)
      || ["cn_platform_video_analysis", "cached_analysis"].includes(cleanText(videoAiAnalysis.reason))
    ),
  );
  const cnAnalysis = asRecord(videoFlow.cn_analysis || resultRow.cn_analysis);
  const cnSummary = cnPlatformVideo ? cleanText(cnAnalysis.content_summary) : "";
  const cnMediaDegraded = cnPlatformVideo && Boolean(videoFlow.media_degraded || resultRow.media_degraded);
  // CN 深析缓存键=<platform>:<video_id>(vkpi_analysis_cache target_id 同款);富面板只在
  // flow 明示分析已就绪(compact cn_analysis 或 ai_analysis=ready)时渲染,未就绪沿用横幅
  // 诚实态不假排队;媒体降级(仅元数据)不渲染。
  const cnVideoId = cleanText(result.video_id || videoFlow.video_id);
  const cnAnalysisTargetId = cnPlatformVideo && platform && cnVideoId ? `${platform}:${cnVideoId}` : "";
  const cnAnalysisReady = cnPlatformVideo && !cnMediaDegraded && (
    Object.keys(cnAnalysis).length > 0
    || cleanText(videoAiAnalysis.state).toLowerCase() === "ready"
  );
  const videoReady = (
    ["ready", "partial", "already_analyzed", "official_channel_video", "cn_platform_video"].includes(cleanText(flowStatus || videoFlow.status).toLowerCase())
    || officialChannelVideo
  ) && !analysisFailed;
  const videoPending = Boolean(isVideo && result.execute && !videoReady && !analysisFailed);
  const executeDone = Boolean(result.execute && (
    isVideo ? videoReady : profileState === "ready"
  ));
  const profileFailed = !isVideo && profileState === "failed";
  const profileRetryable = profileFailed && canExecute;
  const profileStatusCopy = profileStatusCopyFor(profileState);
  const showActionButton = isVideo ? (!result.execute || analysisFailed) : profileRetryable;
  const actionLabel = isVideo
    ? retryableFailure ? "重试分析" : cnPlatformVideo ? "分析此视频（不建档）" : knownCreator ? "只分析此视频" : "建档并分析"
    : "重试抓资料";
  // 抓取软失败(平台反爬拦截 / 私密 / 已删):后端把错误哨兵抬成 metadata_failed + metadata_error,
  // 这里诚实呈现真因,不再一概说「没识别到创作者」——否则用户会误以为是匹配问题,而非抓取被平台挡掉。
  const scrapeUnavailable = Boolean(
    isVideo && (cleanText(flowStatus || videoFlow.status) === "metadata_failed" || cleanText(videoFlow.metadata_error)),
  );
  // 创作者尚未解析但并非失败:等启动分析后由后台识别(视频专用队列),不是「识别不到」。
  const creatorResolutionDeferred = Boolean(
    isVideo && !creatorResolved && !result.execute &&
    ["provider_refresh_pending", "identified", ""].includes(cleanText(videoFlow.status)),
  );
  const disabledReason = scrapeUnavailable
    ? "这条链接抓取失败：被平台反爬拦截，或内容私密/已删，拿不到视频与创作者。换一条公开的视频链接，或稍后重试。"
    : officialChannelVideo || cnPlatformVideo
      ? ""
      : isVideo && !creatorResolved
        ? creatorResolutionDeferred
          ? "创作者会在启动分析后由后台自动识别，点右侧按钮开始。"
          : "没识别到创作者，无法建档。"
        : result.url_type === "unknown"
          ? "暂不支持此链接。支持 YouTube / Instagram / TikTok 的账号或视频链接；Bilibili / 抖音 / 小红书仅支持视频（笔记）链接，只做内容分析、不建档。"
          : "";

  // P7·账号 URL 结果卡:把后端自动抓取的基础资料(头像/粉丝/简介/帖数)合并展示。
  // 优先 profile_flow.profile_data(execute 后写入),缺则用 creator_identity / 顶层 result 字段兜底,
  // 缺值诚实留空,绝不编造粉丝数。点卡片打开右侧 KOL 详情抽屉(onOpenProfile)。
  const profileBasics: Row = {
    avatar_url: safeHttpUrl(creator.avatar_url),
    display_name: kolHumanDisplayName({ ...creator, channel_id: creator.channel_id || result.channel_id }, "创作者"),
    handle: kolHumanPublicHandle({ ...creator, channel_id: creator.channel_id || result.channel_id }),
    platform: creator.platform || result.platform,
    followers: creator.followers ?? creator.subscriber_count,
    posts_count: creator.posts_count ?? creator.video_count,
    bio: creator.bio || creator.description,
    profile_url: firstSafeHttpUrl(creator.profile_url, creator.channel_url, result.url?.normalized, result.url?.input),
  };
  const profileRawFields = publicFieldRows(creator).filter(([key, value]) => (
    key !== "channel_id" && !isOpaqueKolChannelId(value, creator) && !containsOpaqueKolChannelId(value, creator)
  ));
  const hasProfileBasics = !isVideo && [
    profileBasics.avatar_url,
    profileBasics.followers,
    profileBasics.posts_count,
    profileBasics.bio,
    profileBasics.handle,
  ].some((value) => cleanText(value));
  const canOpenProfile = !isVideo && Boolean(onOpenProfile);

  // Profile 默认只抓资料；此操作最多补抓 120 条历史内容，再为已发现的视频排队 final_v1。
  // 状态与 target/request 绑定，A 的慢响应不能覆盖 B 的 loading/done，也不会让 B 的按钮误禁用。
  type FullVideoState = { targetKey: string; kolId: number; requestId: number; status: string; msg: string };
  const fullVideoTargetKey = `${matchedKolId}:${firstSafeHttpUrl(result.url?.normalized, result.url?.input) || cleanText(result.handle)}`;
  const fullVideoRequestId = useRef(0);
  const activeFullVideoTarget = useRef(fullVideoTargetKey);
  if (activeFullVideoTarget.current !== fullVideoTargetKey) {
    activeFullVideoTarget.current = fullVideoTargetKey;
    fullVideoRequestId.current += 1;
  }
  const [storedFullVideoState, setStoredFullVideoState] = useState<FullVideoState>({
    targetKey: fullVideoTargetKey,
    kolId: matchedKolId,
    requestId: 0,
    status: "idle",
    msg: "",
  });
  const fullVideoState = storedFullVideoState.targetKey === fullVideoTargetKey
    ? storedFullVideoState
    : { targetKey: fullVideoTargetKey, kolId: matchedKolId, requestId: fullVideoRequestId.current, status: "idle", msg: "" };
  // 刀2·流2 路A:profile execute 顺带入队了 N 条代表视频 final_v1(account_dossier 据此出 LLM 账号分)。
  // queued>0 → 深度分析进行中,据此诚实化完成横幅(头像粉丝已入库,但 LLM 分要等 worker 跑完代表视频)。
  const deepAnalysisRunning = !isVideo && repQueued > 0 && !accountAiDisabled;
  const discoverAllVideos = async () => {
    const url = cleanText(result.url?.input);
    if (!apiToken || !url || fullVideoState.status === "loading") return;
    const requestId = ++fullVideoRequestId.current;
    const targetKey = fullVideoTargetKey;
    const targetKolId = matchedKolId;
    const commit = (status: string, msg: string) => {
      if (activeFullVideoTarget.current === targetKey && fullVideoRequestId.current === requestId) {
        setStoredFullVideoState({ targetKey, kolId: targetKolId, requestId, status, msg });
      }
    };
    commit("loading", "正在发现该 KOL 的历史视频（最多 120 条）…");
    try {
      const r = await deepCrawlKolUrl(apiToken, url, true, {
        mode: "account_deep", forceFullHistory: true, maxPosts: 120,
        source: "smart_kol_input_full_video", timeoutMs: 300000,
      });
      const kid = (r as any).profile_flow?.kol_pool_id || matchedKolId;
      if (kid) {
        if (accountAiDisabled) {
          commit("done", "已发现历史视频并入库；AI 深析未启用，本轮未创建模型任务");
        } else {
          const enq = await enqueueAllKolVideos(apiToken, kid);
          commit("done", `已发现并入队:${enq.queued ?? 0} 条 final_v1 排队中(进度见左侧任务板)`);
        }
      } else {
        commit("done", "已发现视频并入库,稍后在抽屉查看");
      }
    } catch (e: any) {
      commit("error", e?.message ? String(e.message) : "全视频发现失败");
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-950/[0.10] p-3">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-cyan-100">
              {isVideo ? <Video size={11} /> : <BadgeCheck size={11} />}
              {urlTypeLabel(result.url_type)}
            </span>
            <span className="rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-slate-300">{display(result.platform)}</span>
            <span className="rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-slate-300">
              {result.in_pool ? "库内已有此人" : "库内暂无此人"}
            </span>
            {tiktokRisk ? (
              <span className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1 text-amber-100">
                TikTok 视频有时拿不到，可能需要重试
              </span>
            ) : null}
          </div>
          {isVideo ? (
            <VideoCreatorCard
              creator={creator}
              metadata={metadata}
              onOpen={onOpenProfile && matchedKolId ? () => onOpenProfile(result) : undefined}
            />
          ) : (
            <div className="mt-2 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-2">
              <div className="truncate">对象: <span className="text-slate-200">{display(metadata.title || kolHumanDisplayName({ ...creator, channel_id: creator.channel_id || result.channel_id }, "YouTube 创作者"))}</span></div>
              <div className="truncate">身份: <span className="text-slate-200">{display(kolHumanPublicHandle({ ...creator, channel_id: creator.channel_id || result.channel_id }) || kolHumanDisplayName({ ...creator, channel_id: creator.channel_id || result.channel_id }, "YouTube 创作者"))}</span></div>
            </div>
          )}
          {/* P7·账号 URL 结果卡:头像 + 粉丝(+帖数/简介,有则显)+ 点开右侧详情抽屉;缺值诚实留空,不编造。 */}
          {hasProfileBasics ? (
            <>
              <ProfileInfoCard
                key={`${cleanText(profileBasics.avatar_url)}:${cleanText(profileBasics.handle)}`}
                data={profileBasics}
                apiToken={apiToken}
                onOpen={canOpenProfile ? () => onOpenProfile?.(result) : undefined}
              />
              {profileRawFields.length ? (
                <details className="mt-1.5 rounded-md border border-white/[0.05] bg-black/10 px-2.5 py-1.5 text-[9.5px] text-slate-400">
                  <summary className="cursor-pointer select-none text-slate-500 hover:text-cyan-200">原始字段 {profileRawFields.length}</summary>
                  <div className="mt-2 grid gap-x-3 gap-y-1 border-t border-white/[0.05] pt-2 sm:grid-cols-2">
                    {profileRawFields.map(([key, value]) => (
                      <div key={key} className="flex min-w-0 gap-1.5">
                        <span className="shrink-0 text-slate-600">{key}</span>
                        <span className="min-w-0 flex-1 truncate text-slate-300" title={value}>{value}</span>
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          {showActionButton ? (
            <button
              type="button"
              onClick={onExecute}
              disabled={!canExecute}
              className="inline-flex min-h-[34px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.14] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:border-white/[0.08] disabled:bg-white/[0.04] disabled:text-slate-500"
              title={disabledReason || "确认后执行，任务进侧边栏任务看板"}
            >
              {isExecuting ? <Loader2 size={12} className="animate-spin" /> : isVideo && !knownCreator ? <UserPlus size={12} /> : <Database size={12} />}
              {isExecuting ? "执行中..." : actionLabel}
            </button>
          ) : !isVideo ? (
            <span
              className="inline-flex min-h-[34px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.10] px-3 text-[11px] font-medium text-cyan-100"
              title={profileStatusCopy.title}
              data-testid="profile-crawl-status"
            >
              {profileState === "running" ? <Loader2 size={12} className="animate-spin" /> : profileState === "queued" ? <Clock3 size={12} /> : <Database size={12} />}
              {profileStatusCopy.label}
            </span>
          ) : null}
          {localEvaluationEligible ? (
            <button
              type="button"
              onClick={onLocalEvaluation}
              disabled={!canExecute || isExecuting}
              className="inline-flex min-h-[30px] items-center justify-center gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2.5 text-[10px] font-medium text-amber-100 transition-colors hover:bg-amber-400/[0.14] disabled:cursor-not-allowed disabled:opacity-50"
              title="仅本机评估；结果与生产分析隔离，不代表模型已获生产授权"
              data-testid="video-local-evaluation-action"
            >
              <AlertTriangle size={11} /> 本地评估此视频（非生产）
            </button>
          ) : null}
        </div>
      </div>
      {!isVideo && matchedKolId ? (
        <Suspense
          fallback={(
            <div className="mt-2 inline-flex items-center gap-1.5 text-[10px] text-slate-400" role="status">
              <Loader2 size={11} className="animate-spin" /> 正在载入账号分析数据…
            </div>
          )}
        >
          <AccountUrlInlineOverview
            item={currentAccountItem}
            bundle={currentAccountBundle}
            dossier={currentAccountDossier}
            recommendation={currentRecommendation}
            loading={currentAccountState.status === "loading"}
            error={currentAccountState.status === "error" ? currentAccountState.error : ""}
            freshness={currentFreshness}
          />
        </Suspense>
      ) : null}
      {isVideo && videoResolutionSteps.length ? (
        <div
          className="mt-2 rounded-md border border-cyan-300/15 bg-black/20 px-3 py-2"
          data-testid="video-url-resolution-progress"
        >
          <div className="mb-1.5 text-[10px] font-medium text-cyan-100">视频 URL 处理进度</div>
          <div className="grid gap-1.5 sm:grid-cols-4">
            {videoResolutionSteps.map((step) => {
              const state = cleanText(step.status).toLowerCase() || "pending";
              const running = ["queued", "running", "processing"].includes(state);
              const done = ["ready", "done", "skipped"].includes(state);
              const failed = ["failed", "blocked", "error"].includes(state);
              const stateLabel = running ? "进行中" : state === "skipped" ? "已跳过" : done ? "已完成" : failed ? "未完成" : "待处理";
              return (
                <div
                  key={cleanText(step.key || step.label)}
                  className={`rounded border px-2 py-1.5 text-[9.5px] ${
                    failed
                      ? "border-rose-300/20 bg-rose-400/[0.08] text-rose-100"
                      : done
                        ? "border-emerald-300/20 bg-emerald-400/[0.08] text-emerald-100"
                        : running
                          ? "border-cyan-300/20 bg-cyan-400/[0.08] text-cyan-100"
                          : "border-white/[0.08] bg-white/[0.03] text-slate-400"
                  }`}
                  title={cleanText(step.reason)}
                >
                  <span className="flex items-center gap-1">
                    {running ? <Loader2 size={10} className="animate-spin" /> : null}
                    <span>{display(step.label)}</span>
                  </span>
                  <span className="mt-0.5 block text-[8.5px] opacity-70">{stateLabel}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {isImageCarousel ? (
        <div className="mt-2">
          <div className="mx-auto w-full max-w-2xl">
            <div className="flex snap-x snap-mandatory gap-2 overflow-x-auto rounded-md border border-white/[0.08] bg-black/40 p-2">
              {imageUrls.map((src, i) => (
                <img
                  key={`${src}-${i}`}
                  src={proxiedImageUrl(src) || src}
                  alt={`图 ${i + 1}/${imageUrls.length}`}
                  loading="lazy"
                  className="h-64 w-auto shrink-0 snap-center rounded bg-black object-contain"
                />
              ))}
            </div>
            <div className="mt-1 text-center text-[10px] text-slate-500">图文帖 · {imageUrls.length} 张图 · 左右滑动查看</div>
          </div>
        </div>
      ) : null}
      {isVideo && !isImageCarousel ? (
        <VideoMediaSummary
          platform={platform}
          metadata={metadata}
          youtubeVideoId={youtubeVideoId}
          cachedVideoUrl={cachedVideoUrl}
          sourceUrl={sourceVideoUrl}
        />
      ) : null}
      {isVideo && !isImageCarousel && apiToken && matchedKolId > 0 && Number(effectiveEvidenceId) > 0 ? (
        <Suspense
          fallback={(
            <div className="mt-2 inline-flex items-center gap-1.5 text-[10px] text-slate-400" role="status">
              <Loader2 size={11} className="animate-spin" /> 正在载入评论证据…
            </div>
          )}
        >
          <VideoCommentSamples
            apiToken={apiToken}
            kolPoolId={matchedKolId}
            evidenceId={Number(effectiveEvidenceId)}
            platformCommentCount={metadata.comment_count ?? metadata.comments}
          />
        </Suspense>
      ) : null}
      {isVideo && !isImageCarousel && apiToken && effectiveEvidenceId ? (
        // 分镜/评分独立渲染:不再嵌在 hasPlayableVideo(播放器 URL)闸内——视频文件未就绪也先出时间戳/评分。
        // 图文/轮播帖(isImageCarousel)不跑视频分镜(上面已展示图片轮播)。
        // evidenceId 走顶层兜底,配合 VideoSceneAnalysis 轮询「原地丝滑补上」;历史记录点开同样命中(evidence_id 来自会话项)。
        <div className="mt-2">
          <VideoSceneAnalysis
            key={effectiveEvidenceId}
            apiToken={apiToken}
            evidenceId={effectiveEvidenceId}
            fallbackFailure={analysisFailed ? jobLastError || "后台分析任务已停止" : undefined}
            disabledReason={analysisDisabled ? cleanText(videoAiAnalysis.reason || jobLastError || "ai_disabled") : undefined}
            disabledDetail={analysisDisabled ? cleanText(videoAiAnalysis.detail || videoAiAnalysis.reason_detail) : undefined}
            recommendation={{
              fitScore: currentHydratedItem.viltrox_fit_score,
              fitReason: currentHydratedItem.viltrox_fit_reason,
              dataGrade: currentRecommendation.data_grade,
              dataGradeScore: currentRecommendation.data_grade_score,
              whyRecommended: currentRecommendation.why_recommended,
              signals: asRecord(currentRecommendation.signals),
              profileUpdatedAt: currentHydratedItem.updated_at || currentHydratedItem.last_seen_at,
            }}
            onVideoUrl={handlePolledVideoUrl}
          />
        </div>
      ) : null}
      {isVideo && !isImageCarousel && !effectiveEvidenceId && videoPending ? (
        <div className="mt-2 rounded-md border border-cyan-300/15 bg-cyan-400/[0.06] px-3 py-2 text-[10.5px] text-cyan-100" role="status">
          <span className="inline-flex items-center gap-2"><Loader2 size={12} className="animate-spin" /> 视频基础数据已展示，AI 深析与时间戳正在排队。</span>
        </div>
      ) : null}
      {officialChannelVideo ? (
        <div className="mt-2 rounded-md border border-violet-300/20 bg-violet-400/[0.08] px-2 py-1.5 text-[10.5px] text-violet-100" role="status">
          这是官方自有账号的视频：官方账号不建人选档案，也不做深度分析，仅展示视频基础数据。
        </div>
      ) : null}
      {cnPlatformVideo ? <CnPlatformVideoNotice degraded={cnMediaDegraded} summary={cnSummary} /> : null}
      {cnAnalysisReady && !isImageCarousel && apiToken && cnAnalysisTargetId ? (
        // CN 富面板:与海外视频同款 VideoSceneAnalysis,读 cn_platform_video 缓存的 final_v1 六层
        // (分镜时间线/观众情绪/三值/归因/建议/评分)。摘要横幅保留在上方;不传 recommendation
        // (CN 不建档,无账号 Fit),不传 onVideoUrl(后端仅对 target_type=video 解析 R2 地址)。
        <div className="mt-2">
          <VideoSceneAnalysis
            key={cnAnalysisTargetId}
            apiToken={apiToken}
            evidenceId={cnAnalysisTargetId}
            targetType="cn_platform_video"
          />
        </div>
      ) : null}
      {disabledReason ? (
        <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1.5 text-[10.5px] text-amber-100">
          {disabledReason}
        </div>
      ) : null}
      {executeDone ? (
        <div className={`mt-2 flex items-center gap-2 rounded-md border px-2 py-1.5 text-[10.5px] ${
          flowStatus === "partial"
            ? "border-amber-300/20 bg-amber-400/[0.10] text-amber-100"
            : "border-emerald-300/20 bg-emerald-400/[0.10] text-emerald-100"
        }`}>
          <span className="flex-1">
            {flowStatus === "partial"
              ? (isVideo ? "视频基础数据已入库 · 部分扩展数据未完成" : "资料部分抓取完成，已入库")
              : (isVideo
                  ? officialChannelVideo
                    ? "官方账号视频 · 已保留基础数据，未建档"
                    : cnPlatformVideo
                    ? cnMediaDegraded
                      ? "中国平台视频 · 已保留元数据（视频源不可下载），未建档"
                      : "中国平台视频 · 内容分析完成，未建档"
                    : analysisDisabled
                    ? "视频基础数据已入库 · AI 深析未启用"
                    : "视频基础数据已入库"
                  : deepAnalysisRunning
                    ? `资料已入库 · 账号深度分析进行中(${repQueued} 条代表视频，完成后「查看完整分析」即出 LLM 账号分)`
                    : accountAiDisabled
                      ? "资料已抓取并入库 · AI 深析未启用"
                      : "资料已抓取并入库")}
            {latency ? ` · 耗时 ${latency}` : ""}
          </span>
          {!isVideo ? (
            <button
              type="button"
              disabled={fullVideoState.status === "loading"}
              onClick={() => void discoverAllVideos()}
              className="shrink-0 rounded border border-cyan-300/30 bg-cyan-400/[0.12] px-2 py-0.5 font-medium text-cyan-50 hover:bg-cyan-400/[0.2] disabled:opacity-50"
            >
              {fullVideoState.status === "loading" ? "发现中…" : accountAiDisabled ? "发现历史视频" : "发现历史视频并批量分析"}
            </button>
          ) : null}
          {onOpenProfile && result.matched_kol_pool_id ? (
            <button
              type="button"
              onClick={() => onOpenProfile(result)}
              className="shrink-0 rounded border border-emerald-300/30 bg-emerald-400/[0.12] px-2 py-0.5 font-medium text-emerald-50 hover:bg-emerald-400/[0.2]"
            >
              查看完整分析 →
            </button>
          ) : null}
        </div>
      ) : null}
      {fullVideoState.msg ? (
        <div className={`mt-2 rounded-md border px-2 py-1.5 text-[10.5px] ${
          fullVideoState.status === "error" ? "border-rose-300/20 bg-rose-500/[0.08] text-rose-100" : "border-cyan-300/20 bg-cyan-400/[0.08] text-cyan-100"
        }`}>{fullVideoState.msg}</div>
      ) : null}
      {jobLastError && !officialChannelVideo && !cnPlatformVideo && (!isVideo || !effectiveEvidenceId) ? (
        // officialChannelVideo / cnPlatformVideo 的 ai_analysis.reason 是设计内终态,不是失败,上方横幅已诚实说明。
        <div className="mt-2 rounded-md border border-rose-300/20 bg-rose-500/[0.08] px-2 py-1.5 text-[10.5px] text-rose-100">
          <div>分析失败：{jobFailureCopy.message}</div>
          {jobFailureCopy.code ? <div className="mt-1 font-mono text-[9px] text-rose-200/60">诊断码：{jobFailureCopy.code}</div> : null}
        </div>
      ) : null}
      {!result.execute && actionDescription(result.next_action) ? (
        <div className="mt-2 text-[10px] leading-relaxed text-slate-500">{actionDescription(result.next_action)}</div>
      ) : null}
    </div>
  );
}
