// URL 深析展示子组件(从 SmartKolInputPanel.Sections.tsx 抽出,行为不变)。
// 仅吃 props 的展示组件 + 局部 useState/useEffect:VideoCreatorCard(创作者账号卡)、
// VideoSceneAnalysis(URL 视频内联深析,纯读 final_v1/QA 缓存)、UrlSummary(URL 结果卡容器)。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
// Sections 仍 re-export UrlSummary,容器调用面不变。
import { useEffect, useState } from "react";
import { AlertTriangle, BadgeCheck, Database, Loader2, ShieldCheck, UserPlus, Video } from "lucide-react";

import { type VkpiKolUrlDeepCrawlResponse } from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { deepCrawlKolUrl, enqueueAllKolVideos, getKolVideoAnalysisCache, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";
// A1·复用 KOLVideoAnalysisPanel 的画面质量分 / 三观-归因-建议 / 关键帧 QA 渲染原子(纯读 final_v1/QA 缓存,绝不触 viltrox_fit_score)。
import {
  DeepLayersSection,
  analysisScoreColor,
  compactText,
  finalV1QaPayload,
  firstText,
  normaliseScore,
  qaBoolean,
  qaCheckTags,
  qaIssueItems,
  qaScoreCorrectionText,
  qaStatusClass,
  qaStatusLabel,
  textFrom,
} from "./KOLVideoAnalysisPanel";

import {
  actionDescription,
  asRecord,
  cleanText,
  display,
  durationLabel,
  numberLabel,
  urlTypeLabel,
  videoExecutionDone,
  youtubeEmbedUrl,
  type Row,
} from "./SmartKolInputPanel.helpers";
import { sceneTimelineRowsLocal } from "./SmartKolInputPanel.derivers";
import { ProfileInfoCard } from "./SmartKolInputPanel.Sections";

// A·上框:视频 URL 的创作者账号信息卡。复用 ProfileInfoCard 头像骨架(proxiedImageUrl + onError 渐变圆兜底),
// 数据取 creator_identity,缺失字段用 video_metadata 兜底;点开展开抽屉看该用户全部字段。
function VideoCreatorCard({ creator, metadata }: { creator: Row; metadata: Row }) {
  const [imgError, setImgError] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const avatar = proxiedImageUrl(cleanText(creator.avatar_url));
  const handle = cleanText(creator.handle || creator.display_name || creator.channel_name || metadata.channel_name);
  const platform = cleanText(creator.platform || metadata.platform);
  const channelId = cleanText(creator.channel_id || metadata.channel_id);
  const name = display(handle || channelId || "创作者");
  const followers = numberLabel(creator.followers ?? creator.subscriber_count);
  const bio = cleanText(creator.bio || creator.description || metadata.description);
  const profileUrl = cleanText(creator.profile_url || creator.channel_url);
  const showImg = Boolean(avatar) && !imgError;
  // 全部字段(creator_identity 优先,video_metadata 兜底),空值过滤。
  const allFields = Object.entries({ ...metadata, ...creator })
    .map(([key, value]) => [key, cleanText(value)] as const)
    .filter(([, value]) => Boolean(value));
  return (
    <div className="mt-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2">
      <div className="flex items-start gap-3">
        <span
          className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-[14px] font-bold text-white"
          style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
        >
          {showImg ? (
            <img
              src={avatar}
              alt=""
              className="h-full w-full rounded-full object-cover"
              referrerPolicy="no-referrer"
              onError={() => setImgError(true)}
            />
          ) : (
            name.slice(0, 1).toUpperCase()
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[12px] font-medium text-slate-100">{name}</span>
            {platform ? (
              <span className="shrink-0 rounded border border-white/[0.08] px-1 text-[9px] text-slate-400">{platform}</span>
            ) : null}
            {followers ? (
              <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers} 粉</span>
            ) : null}
          </div>
          {bio ? (
            <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{bio}</p>
          ) : null}
          {profileUrl ? (
            <a
              href={profileUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
            >
              {profileUrl}
            </a>
          ) : null}
        </div>
        {allFields.length ? (
          <button
            type="button"
            onClick={() => setExpanded((cur) => !cur)}
            className="shrink-0 rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-cyan-300/30 hover:text-cyan-100"
          >{expanded ? "收起" : "点开全部字段"}</button>
        ) : null}
      </div>
      {expanded && allFields.length ? (
        <div className="mt-2 grid gap-x-3 gap-y-1 border-t border-white/[0.06] pt-2 text-[10px] sm:grid-cols-2">
          {allFields.map(([key, value]) => (
            <div key={key} className="flex min-w-0 gap-1.5">
              <span className="shrink-0 text-slate-600">{key}</span>
              <span className="min-w-0 flex-1 truncate text-slate-300" title={value}>{value}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// A1·URL 视频内联深析。纯读 final_v1 + final_v1_keyframe_qa 两份缓存(no-store),
// 渲染:画面质量分 layer6(content_quality_score 内容质量 / marketing_value 投放价值)、
// 分镜时间线 layer1、三观/归因/建议 layer3-5(复用 DeepLayersSection)、关键帧 QA。
// 绝不触 viltrox_fit_score:此处只读 final_v1/QA,从不写任何评分。
function VideoSceneAnalysis({ apiToken, evidenceId }: { apiToken: string; evidenceId: string }) {
  const [entry, setEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  const [qaEntry, setQaEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    let attempts = 0;
    setEntry(null);
    setQaEntry(null);
    if (!apiToken || !evidenceId) return undefined;
    // 轮询:视频深析在后台 worker 跑,首拉常未就绪(state!=ready)。每 6s 重拉直到就绪或 ~2.5min 上限,
    // 让分镜/评分「原地丝滑补上」,无需刷新或点详情页。就绪即停;QA 为独立 derive_method,随主轮询附带尝试。
    const poll = () => {
      getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1")
        .then((res) => {
          if (cancelled) return;
          if (res.state === "ready" && res.entry) setEntry(res.entry);
          else if (attempts < 25) {
            attempts += 1;
            timer = window.setTimeout(poll, 6000);
          }
        })
        .catch(() => {
          // 静默降级:读取失败也按未就绪继续轮询(上限内),不打断视频展示。
          if (!cancelled && attempts < 25) {
            attempts += 1;
            timer = window.setTimeout(poll, 6000);
          }
        });
      // 关键帧 QA 是独立 derive_method;缺它不影响 final_v1 主体渲染(独立 try)。
      getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1_keyframe_qa")
        .then((res) => {
          if (cancelled) return;
          if (res.state === "ready" && res.entry) setQaEntry(res.entry);
        })
        .catch(() => {
          // QA 缺失静默:只是少一块复核信息,不阻断主分析展示。
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [apiToken, evidenceId]);
  const result = asRecord(entry?.result);
  const payload = asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
  const layer1 = asRecord(payload.layer1_visual_content);
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer4 = asRecord(payload.layer4_attribution);
  const layer5 = asRecord(payload.layer5_recommendations);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  // 画面质量分:content_quality_score=内容质量,marketing_value=投放价值(口径与 KOLVideoAnalysisPanel.AnalysisCard 一致)。
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || textFrom(layer6.key_hook);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const riskText = textFrom(layer6.risk_flags);
  const contentSummary = cleanText(layer1.content_summary);
  const sceneTimeline = sceneTimelineRowsLocal(layer1.scene_timeline);
  const hasScores = contentScore.score != null || marketingScore.score != null;
  // 关键帧 QA(复用面板口径):pass/checks/issues/纠偏建议。
  const qaPayload = finalV1QaPayload(qaEntry);
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaPass = qaBoolean(qaPayload.qa_pass ?? asRecord(qaEntry?.result).qa_pass);
  const qaBadgeText = qaPass === false ? "需复核" : qaPass === true ? "通过" : "未定";
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  if (!hasScores && !contentSummary && !sceneTimeline.length && !qaHasPayload) return null;
  return (
    <div className="mt-2 space-y-2">
      {hasScores ? (
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border border-white/[0.05] bg-black/25 px-2.5 py-2">
            <div className="mb-1 text-[9px] text-slate-500">内容质量</div>
            <div className="text-[22px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(contentScore.score) }}>{contentScore.score ?? "—"}</div>
          </div>
          <div className="rounded-md border border-white/[0.05] bg-black/25 px-2.5 py-2">
            <div className="mb-1 text-[9px] text-slate-500">投放价值</div>
            <div className="text-[22px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(marketingScore.score) }}>{marketingScore.score ?? "—"}</div>
          </div>
        </div>
      ) : null}
      {verdict ? (
        <div className="text-[10.5px] leading-relaxed text-slate-300">{compactText(verdict, 180)}</div>
      ) : null}
      {contentSummary ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.02] px-2.5 py-2">
          <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">内容概述</div>
          <div className="text-[10.5px] leading-relaxed text-slate-300">{contentSummary.length > 240 ? `${contentSummary.slice(0, 240)}...` : contentSummary}</div>
        </div>
      ) : null}
      {sceneTimeline.length ? (
        <div className="rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
          <div className="mb-1.5 text-[9px] uppercase tracking-wider text-slate-500">分镜时间线</div>
          <div className="space-y-1">
            {sceneTimeline.map((row) => (
              <div key={row.key} className="flex items-start gap-2 text-[10px] leading-relaxed">
                <span className="shrink-0 rounded bg-cyan-500/12 px-1.5 py-0.5 font-mono tabular-nums text-cyan-200">{row.timestamp || "—"}</span>
                <span className="text-slate-300">{row.what || "—"}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {viewerReaction || riskText ? (
        <div className="flex flex-wrap gap-1.5 text-[9.5px]">
          {viewerReaction ? <span className="rounded bg-purple-500/10 px-2 py-1 text-purple-200">心动: {compactText(viewerReaction, 54)}</span> : null}
          {riskText ? <span className="rounded bg-amber-500/10 px-2 py-1 text-amber-200">风险: {compactText(riskText, 60)}</span> : null}
        </div>
      ) : null}
      <DeepLayersSection layer3={layer3} layer4={layer4} layer5={layer5} />
      {qaHasPayload ? (
        <div className={`rounded-md border p-2 ${qaPass === false ? "border-rose-400/20 bg-rose-500/[0.045]" : "border-emerald-400/15 bg-emerald-500/[0.035]"}`}>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[9px] font-medium ${qaPass === false ? "bg-rose-500/15 text-rose-200" : "bg-emerald-500/15 text-emerald-200"}`}>
              {qaPass === false ? <AlertTriangle size={10} /> : <ShieldCheck size={10} />}
              关键帧 QA {qaBadgeText}
            </span>
            {Number.isFinite(qaConfidence) ? <span className="text-[9px] text-slate-500">置信 {Math.round(qaConfidence * 100)}%</span> : null}
          </div>
          {qaSummary ? <div className="mb-1.5 text-[10px] leading-relaxed text-slate-200">{compactText(qaSummary, 150)}</div> : null}
          {qaChecks.length ? (
            <div className="mb-1.5 flex flex-wrap gap-1">
              {qaChecks.map((check) => (
                <span key={check.key} className={`rounded border px-1.5 py-0.5 text-[8.5px] ${qaStatusClass(check.status)}`} title={check.detail || undefined}>
                  {check.label}: {qaStatusLabel(check.status)}
                </span>
              ))}
            </div>
          ) : null}
          {qaIssues.slice(0, 2).map((issue) => (
            <div key={issue.key} className="mb-1 rounded border border-white/[0.05] bg-black/20 px-2 py-1 text-[9.5px] text-slate-300">
              <span className="text-amber-200">{issue.label}</span>
              {issue.evidence ? <span> · {compactText(issue.evidence, 90)}</span> : null}
              {issue.correction ? <span className="text-cyan-200"> · {compactText(issue.correction, 70)}</span> : null}
            </div>
          ))}
          {qaCorrection ? <div className="text-[9.5px] text-slate-400">纠偏建议: {compactText(qaCorrection, 150)}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

export function UrlSummary({
  result,
  apiToken,
  canExecute,
  isExecuting,
  onExecute,
  onOpenProfile,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
  apiToken: string;
  canExecute: boolean;
  isExecuting: boolean;
  onExecute: () => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
}) {
  const profileFlow = asRecord(result.profile_flow);
  const videoFlow = asRecord(result.video_flow);
  const creator = asRecord(result.creator_identity || videoFlow.creator_identity);
  const metadata = asRecord(result.video_metadata || videoFlow.video_metadata);
  const analysis = asRecord(videoFlow.analysis);
  const jobLastError = cleanText(videoFlow.job_last_error || profileFlow.job_last_error);
  const jobStatus = cleanText(videoFlow.job_status || profileFlow.job_status || videoFlow.status || profileFlow.status);
  const flowStatus = cleanText(videoFlow.status || profileFlow.status || (result.search_session ? asRecord(result.search_session).item_status : ""));
  const latency = durationLabel(analysis.latency_ms);
  const platform = cleanText(result.platform).toLowerCase();
  const isVideo = result.url_type === "video";
  // 顶层兜底:execute 响应把 cached_video_url/evidence_id 摊平到 result 顶层(不在嵌套 video_flow 下),
  // 旧码只读 videoFlow.* → 取空 → 播放器/分镜静默不渲染。两处都加 result.* 兜底。
  const cachedVideoUrl = cleanText(videoFlow.cached_video_url || asRecord(result).cached_video_url);
  const effectiveEvidenceId = String(videoFlow.evidence_id ?? asRecord(result).evidence_id ?? "").trim();
  const youtubeVideoId = cleanText(result.video_id || videoFlow.video_id);
  const videoPoster = proxiedImageUrl(cleanText(metadata.thumbnail_url));
  const hasPlayableVideo = isVideo && (platform === "youtube" ? Boolean(youtubeVideoId) : Boolean(cachedVideoUrl));
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
  const retryableFailure = Boolean(isVideo && jobLastError && ["failed", "blocked"].includes(jobStatus));
  const executeDone = result.execute && (
    isVideo ? videoExecutionDone(flowStatus || videoFlow.status) : cleanText(profileFlow.status) === "ready"
  );
  // profile 已改自动 execute(识别即自动抓资料入库),手动按钮只在 profile 抓取失败时作「重试」兜底。
  const profileFailed = !isVideo && ["crawl_failed", "failed"].includes(cleanText(profileFlow.status));
  const profileRetryable = profileFailed && canExecute;
  // 账号资料自动抓取中(用户贴 URL → 自动 execute,无二次确认):展示自动状态,不再显示「抓基础资料」按钮。
  const profileAutoRunning = !isVideo && isExecuting;
  const showActionButton = isVideo || profileRetryable;
  const actionLabel = isVideo
    ? retryableFailure ? "重试分析" : knownCreator ? "只分析此视频" : "建档并分析"
    : "重试抓资料";
  // 抓取软失败(平台反爬拦截 / 私密 / 已删):后端把错误哨兵抬成 metadata_failed + metadata_error,
  // 这里诚实呈现真因,不再一概说「没识别到创作者」——否则用户会误以为是匹配问题,而非抓取被平台挡掉。
  const scrapeUnavailable = Boolean(
    isVideo && (cleanText(flowStatus || videoFlow.status) === "metadata_failed" || cleanText(videoFlow.metadata_error)),
  );
  const disabledReason = scrapeUnavailable
    ? "这条链接抓取失败：被平台反爬拦截，或内容私密/已删，拿不到视频与创作者。换一条公开的视频链接，或稍后重试。"
    : isVideo && !creatorResolved
      ? "没识别到创作者，无法建档。"
      : result.url_type === "unknown"
        ? "识别不了这个链接。"
        : "";

  // P7·账号 URL 结果卡:把后端自动抓取的基础资料(头像/粉丝/简介/帖数)合并展示。
  // 优先 profile_flow.profile_data(execute 后写入),缺则用 creator_identity / 顶层 result 字段兜底,
  // 缺值诚实留空,绝不编造粉丝数。点卡片打开右侧 KOL 详情抽屉(onOpenProfile)。
  const profileData = asRecord(profileFlow.profile_data);
  const profileBasics: Row = {
    avatar_url: profileData.avatar_url ?? creator.avatar_url,
    handle: profileData.handle ?? creator.handle ?? result.handle,
    platform: profileData.platform ?? creator.platform ?? result.platform,
    followers: profileData.followers ?? creator.followers ?? creator.subscriber_count,
    posts_count: profileData.posts_count ?? creator.posts_count,
    bio: profileData.bio ?? creator.bio ?? creator.description,
    profile_url: profileData.profile_url ?? creator.profile_url ?? creator.channel_url ?? result.url?.normalized,
  };
  const hasProfileBasics = !isVideo && [
    profileBasics.avatar_url,
    profileBasics.followers,
    profileBasics.posts_count,
    profileBasics.bio,
    profileBasics.handle,
  ].some((value) => cleanText(value));
  const canOpenProfile = !isVideo && Boolean(onOpenProfile);

  // 项⑥:profile 默认只抓资料(profile_basics),不发现视频。这个按钮用 account_deep+force_full_history
  // 把该 KOL 全部历史视频 materialize,再 enqueueAllKolVideos 跑 final_v1。
  const [fullVideoState, setFullVideoState] = useState<{ status: string; msg: string }>({ status: "idle", msg: "" });
  const matchedKolId = (result as any).matched_kol_pool_id;
  // 刀2·流2 路A:profile execute 顺带入队了 N 条代表视频 final_v1(account_dossier 据此出 LLM 账号分)。
  // queued>0 → 深度分析进行中,据此诚实化完成横幅(头像粉丝已入库,但 LLM 分要等 worker 跑完代表视频)。
  const repAnalysis = asRecord((result as any).representative_video_analysis);
  const repQueued = Number(repAnalysis.queued ?? 0);
  const deepAnalysisRunning = !isVideo && repQueued > 0;
  const discoverAllVideos = async () => {
    const url = cleanText(result.url?.input);
    if (!apiToken || !url || fullVideoState.status === "loading") return;
    setFullVideoState({ status: "loading", msg: "正在发现该 KOL 全部历史视频…" });
    try {
      const r = await deepCrawlKolUrl(apiToken, url, true, {
        mode: "account_deep", forceFullHistory: true, maxPosts: 120,
        source: "smart_kol_input_full_video", timeoutMs: 300000,
      });
      const kid = (r as any).profile_flow?.kol_pool_id || matchedKolId;
      if (kid) {
        const enq = await enqueueAllKolVideos(apiToken, kid);
        setFullVideoState({ status: "done", msg: `已发现并入队:${enq.queued ?? 0} 条 final_v1 排队中(进度见左侧任务板)` });
      } else {
        setFullVideoState({ status: "done", msg: "已发现视频并入库,稍后在抽屉查看" });
      }
    } catch (e: any) {
      setFullVideoState({ status: "error", msg: e?.message ? String(e.message) : "全视频发现失败" });
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
            <VideoCreatorCard creator={creator} metadata={metadata} />
          ) : (
            <div className="mt-2 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-2">
              <div className="truncate">对象: <span className="text-slate-200">{display(metadata.title || creator.display_name || creator.handle || result.handle || result.video_id)}</span></div>
              <div className="truncate">身份: <span className="text-slate-200">{display(creator.channel_id || creator.handle || result.channel_id || result.handle)}</span></div>
            </div>
          )}
          {/* P7·账号 URL 结果卡:头像 + 粉丝(+帖数/简介,有则显)+ 点开右侧详情抽屉;缺值诚实留空,不编造。 */}
          {hasProfileBasics ? (
            <ProfileInfoCard
              data={profileBasics}
              apiToken={apiToken}
              onOpen={canOpenProfile ? () => onOpenProfile?.(result) : undefined}
            />
          ) : null}
        </div>
        <div className="shrink-0">
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
              title="账号 URL 已自动抓取基础资料并入库，无需手动确认"
            >
              {profileAutoRunning ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />}
              {profileAutoRunning ? "自动抓资料中..." : "已自动抓资料入库"}
            </span>
          ) : null}
        </div>
      </div>
      {hasPlayableVideo ? (
        <div className="mt-2">
          <div className="mx-auto w-full max-w-2xl overflow-hidden rounded-md border border-white/[0.08] bg-black/40">
            <div className="relative w-full" style={{ aspectRatio: "16 / 9" }}>
              {platform === "youtube" ? (
                <iframe
                  src={youtubeEmbedUrl(youtubeVideoId)}
                  title={display(metadata.title || result.video_id)}
                  className="absolute inset-0 h-full w-full"
                  allow="autoplay; encrypted-media; picture-in-picture"
                  allowFullScreen
                />
              ) : (
                <video
                  src={cachedVideoUrl}
                  poster={videoPoster || undefined}
                  controls
                  playsInline
                  preload="metadata"
                  className="absolute inset-0 h-full w-full bg-black object-contain"
                />
              )}
            </div>
          </div>
        </div>
      ) : null}
      {isVideo && apiToken && effectiveEvidenceId ? (
        // 分镜/评分独立渲染:不再嵌在 hasPlayableVideo(播放器 URL)闸内——视频文件未就绪也先出时间戳/评分。
        // evidenceId 走顶层兜底,配合 VideoSceneAnalysis 轮询「原地丝滑补上」;历史记录点开同样命中(evidence_id 来自会话项)。
        <div className="mt-2">
          <VideoSceneAnalysis apiToken={apiToken} evidenceId={effectiveEvidenceId} />
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
              ? (isVideo ? "视频分析部分完成，已入库" : "资料部分抓取完成，已入库")
              : (isVideo
                  ? "视频分析完成，已入库"
                  : deepAnalysisRunning
                    ? `资料已入库 · 账号深度分析进行中(${repQueued} 条代表视频，完成后「查看完整分析」即出 LLM 账号分)`
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
              {fullVideoState.status === "loading" ? "发现中…" : "发现并分析全部视频"}
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
      {jobLastError ? (
        <div className="mt-2 rounded-md border border-rose-300/20 bg-rose-500/[0.08] px-2 py-1.5 text-[10.5px] text-rose-100">
          分析失败: {jobLastError}
        </div>
      ) : null}
      {!result.execute && actionDescription(result.next_action) ? (
        <div className="mt-2 text-[10px] leading-relaxed text-slate-500">{actionDescription(result.next_action)}</div>
      ) : null}
    </div>
  );
}
