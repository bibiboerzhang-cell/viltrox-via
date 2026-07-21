import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Clock3, Loader2, ShieldCheck } from "lucide-react";

import {
  getKolVideoAnalysisCache,
  type VkpiKolVideoAnalysisCacheEntry,
} from "../../../../services/vkpi/kolPool-api";
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
import { sceneTimelineRowsLocal } from "./SmartKolInputPanel.derivers";
import { asRecord, cleanText, type Row } from "./SmartKolInputPanel.helpers";
import { humanizeLlmReason } from "../llmReasonCopy";

// A1·URL 视频内联深析。纯读 final_v1 + final_v1_keyframe_qa 两份缓存(no-store),
// 渲染:画面质量分 layer6(content_quality_score 内容质量 / marketing_value 投放价值)、
// 分镜时间线 layer1、三观/归因/建议 layer3-5(复用 DeepLayersSection)、关键帧 QA。
// 绝不触 viltrox_fit_score:此处只读 final_v1/QA,从不写任何评分。
type AnalysisNotice = {
  state: "pending" | "ready" | "paused" | "blocked" | "failed" | "not_requested" | "unavailable";
  reason?: string;
  detail?: string;
  provider?: string;
  stage?: string;
};

export type VideoRecommendationContext = {
  fitScore?: unknown;
  fitReason?: unknown;
  dataGrade?: unknown;
  dataGradeScore?: unknown;
  whyRecommended?: unknown;
  signals?: Row;
  profileUpdatedAt?: unknown;
};

function dateTimeLabel(value: unknown): string {
  const raw = cleanText(value);
  if (!raw) return "";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function finiteScore(value: unknown): number | null {
  if (value == null || (typeof value === "string" && !value.trim())) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(100, Math.round(parsed)));
}

function stageBadgeClass(state: "ready" | "pending" | "blocked" | "review") {
  if (state === "ready") return "border-emerald-300/20 bg-emerald-400/[0.08] text-emerald-100";
  if (state === "blocked") return "border-slate-300/15 bg-white/[0.035] text-slate-400";
  if (state === "review") return "border-rose-300/20 bg-rose-400/[0.08] text-rose-100";
  return "border-cyan-300/15 bg-cyan-400/[0.06] text-cyan-100";
}

function VideoDecisionOverview({
  notice,
  entry,
  hasAnalysisContent,
  marketingScore,
  qaPass,
  qaHasPayload,
  sceneCount,
  recommendation,
  payload,
}: {
  notice: AnalysisNotice;
  entry: VkpiKolVideoAnalysisCacheEntry | null;
  hasAnalysisContent: boolean;
  marketingScore: ReturnType<typeof normaliseScore>;
  qaPass: boolean | null;
  qaHasPayload: boolean;
  sceneCount: number;
  recommendation: VideoRecommendationContext;
  payload: Row;
}) {
  const fitScore = finiteScore(recommendation.fitScore);
  const dataGrade = cleanText(recommendation.dataGrade).toUpperCase();
  const dataGradeScore = Number(recommendation.dataGradeScore);
  const signals = asRecord(recommendation.signals);
  const videoRecommendationReady = marketingScore.score != null;
  const accountRecommendationReady = fitScore != null;
  const recommendationReady = videoRecommendationReady || accountRecommendationReady;
  const result = asRecord(entry?.result);
  const evaluationOnly = isEvaluationOnlyAnalysis(result, payload);
  const recommendationDetail = videoRecommendationReady && accountRecommendationReady
    ? "视频模型分 + 账号 Fit · outcome 未验证"
    : videoRecommendationReady
      ? `${evaluationOnly ? "本地评估" : "视频模型"}分 · outcome 未验证`
      : accountRecommendationReady
        ? "账号 Fit · outcome 未验证"
        : "";
  const analysisTerminal = ["blocked", "failed", "not_requested", "unavailable"].includes(notice.state);
  const analysisFinished = notice.state === "ready" || hasAnalysisContent;
  const analysisState = hasAnalysisContent ? "ready" : analysisTerminal ? "blocked" : "pending";
  const qaState = qaPass === true
    ? "ready"
    : qaPass === false || qaHasPayload
      ? "review"
      : analysisTerminal
        ? "blocked"
        : "pending";
  const recommendationState = recommendationReady ? "ready" : analysisFinished || analysisTerminal ? "blocked" : "pending";
  const stages: Array<{ label: string; detail: string; state: "ready" | "pending" | "blocked" | "review" }> = [
    { label: "基础数据", detail: "已入库", state: "ready" },
    {
      label: "分镜深析",
      detail: hasAnalysisContent ? `${sceneCount} 个时间点` : analysisTerminal ? "未启用/已停止" : "后台生成中",
      state: analysisState,
    },
    {
      label: "推荐度",
      detail: recommendationReady
        ? recommendationDetail
        : analysisFinished ? "结果未含评分" : analysisTerminal ? "暂无可用评分" : "等待分析",
      state: recommendationState,
    },
    {
      label: "证据 QA",
      detail: qaPass === true ? "已通过" : qaPass === false ? "需人工复核" : qaHasPayload ? "结果未定" : "待补",
      state: qaState,
    },
  ];
  const provenance = {
    ...asRecord(result.provenance),
    ...asRecord(payload.provenance),
  };
  const model = cleanText(entry?.model || result.model || provenance.model);
  const method = cleanText(result.method || provenance.method || entry?.derive_method);
  const claimStatus = cleanText(result.claim_status || payload.claim_status || provenance.claim_status);
  const analysisUpdatedAt = dateTimeLabel(entry?.updated_at || entry?.created_at);
  const profileUpdatedAt = dateTimeLabel(recommendation.profileUpdatedAt);
  const whyRecommended = cleanText(recommendation.whyRecommended);
  const fitReason = cleanText(recommendation.fitReason);
  const signalParts = [
    Number(signals.videos) > 0 ? `视频证据 ${Number(signals.videos)}` : "",
    Number(signals.analyses) > 0 ? `深析 ${Number(signals.analyses)}` : "",
    Number(signals.projects) > 0 ? `合作项目 ${Number(signals.projects)}` : "",
  ].filter(Boolean);

  return (
    <section className="overflow-hidden rounded-lg border border-cyan-300/15 bg-cyan-950/[0.12]" data-testid="video-decision-overview">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-white/[0.06] px-3 py-2.5">
        <div>
          <div className="text-[10.5px] font-semibold text-slate-100">视频分析与推荐进度</div>
          <div className="mt-0.5 text-[9px] text-slate-500">基础数据先展示；深析、推荐和 QA 按真实完成状态陆续补齐</div>
        </div>
        <div className="text-right text-[8.5px] leading-relaxed text-slate-500">
          {analysisUpdatedAt ? <div>分析更新 {analysisUpdatedAt}</div> : null}
          {!analysisUpdatedAt && profileUpdatedAt ? <div>档案更新 {profileUpdatedAt}</div> : null}
          <div>无评分时不补估值</div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 p-2.5 sm:grid-cols-4">
        {stages.map((stage) => (
          <div key={stage.label} className={`rounded-md border px-2 py-2 ${stageBadgeClass(stage.state)}`}>
            <div className="flex items-center gap-1 text-[9px] font-medium">
              {stage.state === "ready" ? <CheckCircle2 size={10} /> : stage.state === "pending" ? <Loader2 size={10} className="animate-spin" /> : <Clock3 size={10} />}
              {stage.label}
            </div>
            <div className="mt-1 text-[8.5px] opacity-70">{stage.detail}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-px border-t border-white/[0.05] bg-white/[0.04] sm:grid-cols-3">
        <div className="bg-slate-950/55 px-3 py-2.5">
          <div className="text-[8.5px] text-slate-500">当前视频投放价值</div>
          <div className="mt-1 text-[20px] font-bold tabular-nums" style={{ color: analysisScoreColor(marketingScore.score) }}>
            {marketingScore.score ?? "待深析"}
          </div>
          <div className="mt-1 text-[8px] text-slate-600">
            来源: {evaluationOnly ? "本地评估 final_v1（非生产）" : "video_analysis_final_v1"}
          </div>
        </div>
        <div className="bg-slate-950/55 px-3 py-2.5">
          <div className="text-[8.5px] text-slate-500">账号 Fit（库内）</div>
          <div className="mt-1 text-[20px] font-bold tabular-nums" style={{ color: analysisScoreColor(fitScore) }}>
            {fitScore ?? "未形成"}
          </div>
          <div className="mt-1 line-clamp-2 text-[8px] text-slate-600">{fitReason || "没有已验证的账号 Fit 结论"}</div>
        </div>
        <div className="bg-slate-950/55 px-3 py-2.5">
          <div className="text-[8.5px] text-slate-500">数据可信度档（非 Fit）</div>
          <div className="mt-1 text-[20px] font-bold text-cyan-100">{dataGrade || "—"}</div>
          <div className="mt-1 text-[8px] text-slate-600">
            {Number.isFinite(dataGradeScore) ? `${dataGradeScore}/4 完整度` : "等待推荐卡证据"}
          </div>
        </div>
      </div>

      <div
        className="border-t border-amber-300/10 bg-amber-400/[0.035] px-3 py-1.5 text-[8.5px] text-amber-100/75"
        data-testid="business-outcome-status"
        role="note"
      >
        业务 outcome：未验证 · 视频模型分、账号 Fit 与数据完整度均不代表真实销售或投放效果
      </div>

      {(whyRecommended || signalParts.length || model || method || claimStatus || analysisUpdatedAt) ? (
        <div className="border-t border-white/[0.05] px-3 py-2 text-[8.5px] leading-relaxed text-slate-500">
          {whyRecommended ? <div><span className="text-slate-400">证据摘要:</span> {whyRecommended}</div> : null}
          {signalParts.length ? <div className="mt-0.5">证据沉淀: {signalParts.join(" · ")}</div> : null}
          {(model || method || claimStatus || analysisUpdatedAt) ? (
            <div className="mt-0.5">
              {[model && `模型 ${model}`, method && `方法 ${method}`, claimStatus && `结论 ${claimStatus}`, analysisUpdatedAt && `时间 ${analysisUpdatedAt}`].filter(Boolean).join(" · ")}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function isEvaluationOnlyAnalysis(result: Row, payload: Row): boolean {
  const metadata = [
    result,
    payload,
    asRecord(result.llm_execution),
    asRecord(payload.llm_execution),
    asRecord(result.provenance),
    asRecord(payload.provenance),
  ];
  const evaluationOnly = metadata.some((value) => (
    value.evaluation_only === true || cleanText(value.evaluation_only).toLowerCase() === "true"
  ));
  const evaluationScope = metadata.some((value) => (
    cleanText(value.execution_class).toLowerCase() === "local_evaluation"
    || cleanText(value.authorization_scope).toLowerCase() === "evaluation_only"
  ));
  // claim_status=descriptive_only applies to production content too; model
  // readiness is an independent field and must not be used to relabel a
  // production result as local evaluation.
  return evaluationOnly || evaluationScope;
}

export function VideoSceneAnalysis({
  apiToken,
  evidenceId,
  targetType = "video",
  fallbackFailure,
  disabledReason,
  disabledDetail,
  recommendation = {},
  onVideoUrl,
}: {
  apiToken: string;
  // 海外 evidence:数字 id + targetType="video";CN 平台视频:targetType="cn_platform_video",
  // evidenceId 传缓存键 "<platform>:<video_id>"。两者读同一 final_v1 六层结构,渲染同款面板。
  evidenceId: string;
  targetType?: string;
  fallbackFailure?: string;
  disabledReason?: string;
  disabledDetail?: string;
  recommendation?: VideoRecommendationContext;
  onVideoUrl?: (url: string) => void;
}) {
  const [entry, setEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  const [qaEntry, setQaEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  const [notice, setNotice] = useState<AnalysisNotice>({ state: "pending" });
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const startedAt = Date.now();
    setEntry(null);
    setQaEntry(null);
    setNotice({ state: "pending" });
    if (!apiToken || !evidenceId) return undefined;
    const terminalStates = new Set(["blocked", "failed", "error", "cancelled", "canceled"]);
    const schedule = () => {
      if (cancelled) return;
      const elapsed = Date.now() - startedAt;
      if (elapsed >= 30 * 60_000) {
        setNotice({ state: "paused", detail: "后台任务仍未完成，页面已停止自动刷新；稍后重新打开即可继续查看。" });
        return;
      }
      timer = window.setTimeout(poll, elapsed < 120_000 ? 6000 : 10000);
    };
    // 轮询只服务 queued/running/retrying/processing；blocked/failed 会立即停止并呈现真实原因。
    // ready 后展示 final_v1 + 时间戳；读取异常有限重试，不再静默伪装成“已完成”。
    const poll = () => {
      getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1", { targetType })
        .then((res) => {
          if (cancelled) return;
          // R2 缓存视频地址由后端按 evidence 解析,与分析就绪无关 —— 视频已缓存即可先点亮播放器,
          // 不必等分镜跑完。上抛给父组件合并进 cachedVideoUrl(根治历史重建丢地址)。
          const vurl = cleanText(res.cached_video_url);
          if (vurl && onVideoUrl) onVideoUrl(vurl);
          const job = asRecord(res.analysis_job);
          const responseState = cleanText(res.state).toLowerCase();
          const jobState = cleanText(job.state || job.status).toLowerCase();
          const terminalState = terminalStates.has(jobState) ? jobState : terminalStates.has(responseState) ? responseState : "";
          const notRequested = jobState === "not_requested" || responseState === "not_requested" || Boolean(disabledReason);
          const reason = cleanText(job.reason || job.error_category || disabledReason);
          const detail = cleanText(job.reason_detail || disabledDetail);
          const provider = cleanText(job.provider);
          const stage = cleanText(job.stage);
          if (res.state === "ready" && res.entry) {
            setEntry(res.entry);
            setNotice({ state: "ready", provider, stage });
          } else if (notRequested) {
            setNotice({
              state: "not_requested",
              reason: reason || "analysis_not_requested",
              detail: detail || "当前只有视频基础数据，尚未提交 AI 深析与时间戳任务。",
              provider,
              stage,
            });
          } else if (terminalState) {
            setNotice({
              state: terminalState === "blocked" ? "blocked" : "failed",
              reason: reason || fallbackFailure || "后台分析任务未完成",
              detail,
              provider,
              stage,
            });
          } else if (fallbackFailure) {
            setNotice({ state: "failed", reason: fallbackFailure, provider, stage });
          } else {
            setNotice({ state: "pending", reason, detail, provider, stage });
            schedule();
          }
        })
        .catch(() => {
          if (cancelled) return;
          if (Date.now() - startedAt >= 30 * 60_000) {
            setNotice({ state: "unavailable", detail: "分析状态暂时无法读取，请稍后重新打开。" });
            return;
          }
          setNotice({ state: "pending", detail: "分析状态读取中断，正在重试。" });
          schedule();
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [apiToken, disabledDetail, disabledReason, evidenceId, fallbackFailure, onVideoUrl, targetType]);
  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    let attempts = 0;
    setQaEntry(null);
    if (!apiToken || !evidenceId) return undefined;
    const activeStates = new Set(["pending", "queued", "running", "retrying", "processing", "unknown"]);
    // QA 是 final_v1 的独立阶段；主分析先 ready 时也要继续补拉 QA，避免用户必须刷新页面。
    // not_requested 只短暂观察 5 次，给刚落 final_v1、QA job 尚未登记的窄窗口；没有任务时不会长轮询。
    const pollQa = () => {
      getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1_keyframe_qa", { targetType })
        .then((res) => {
          if (cancelled) return;
          const state = cleanText(res.state).toLowerCase();
          if (state === "ready" && res.entry) {
            setQaEntry(res.entry);
            return;
          }
          const maxAttempts = state === "not_requested" ? 5 : 25;
          if ((activeStates.has(state) || state === "not_requested") && attempts < maxAttempts) {
            attempts += 1;
            timer = window.setTimeout(pollQa, 6000);
          }
        })
        .catch(() => {
          if (cancelled || attempts >= 5) return;
          attempts += 1;
          timer = window.setTimeout(pollQa, 6000);
        });
    };
    pollQa();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [apiToken, evidenceId, targetType]);
  const result = asRecord(entry?.result);
  const payload = asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
  const evaluationOnly = notice.state === "ready" && isEvaluationOnlyAnalysis(result, payload);
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
  const hasAnalysisContent = hasScores
    || Boolean(contentSummary || verdict || viewerReaction || riskText)
    || sceneTimeline.length > 0
    || [layer3, layer4, layer5].some((layer) => Object.keys(layer).length > 0)
    || qaHasPayload;
  const overview = (
    <VideoDecisionOverview
      notice={notice}
      entry={entry}
      hasAnalysisContent={hasAnalysisContent}
      marketingScore={marketingScore}
      qaPass={qaPass}
      qaHasPayload={qaHasPayload}
      sceneCount={sceneTimeline.length}
      recommendation={recommendation}
      payload={payload}
    />
  );
  if (!hasAnalysisContent) {
    if (["blocked", "failed"].includes(notice.state)) {
      const failureCopy = humanizeLlmReason(notice.reason, "后台分析任务已停止，请检查模型授权与预算状态。");
      const diagnosticCode = failureCopy.code || (notice.detail ? cleanText(notice.reason) : "");
      return (
        <div className="mt-2 space-y-2">
          {overview}
          <div className="rounded-md border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[10.5px] text-rose-100" role="status">
            <div className="flex items-center gap-1.5 font-medium"><AlertTriangle size={12} /> AI 分析未完成</div>
            <div className="mt-1 leading-relaxed text-rose-100/85">{notice.detail || failureCopy.message}</div>
            {(notice.provider || notice.stage || diagnosticCode) ? (
              <div className="mt-1 text-[9px] text-rose-200/60">
                {[notice.provider && `服务 ${notice.provider}`, notice.stage && `阶段 ${notice.stage}`, diagnosticCode && `诊断码 ${diagnosticCode}`].filter(Boolean).join(" · ")}
              </div>
            ) : null}
          </div>
        </div>
      );
    }
    if (notice.state === "unavailable") {
      return (
        <div className="mt-2 space-y-2">
          {overview}
          <div className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-3 py-2 text-[10.5px] text-amber-100" role="status">
            {notice.detail}
          </div>
        </div>
      );
    }
    if (notice.state === "not_requested") {
      const disabledByPolicy = /ai_disabled|readiness_not_production_ready|model_binding_blocked|operator_disabled/i.test(
        `${notice.reason || ""} ${notice.detail || ""}`,
      );
      return (
        <div className="mt-2 space-y-2">
          {overview}
          <div className="rounded-md border border-slate-300/15 bg-white/[0.035] px-3 py-2 text-[10.5px] text-slate-300" role="status">
            <div className="flex items-center gap-1.5 font-medium"><Clock3 size={12} /> {disabledByPolicy ? "AI 深析未启用" : "AI 深析尚未入队"}</div>
            <div className="mt-1 text-[9.5px] leading-relaxed text-slate-500">
              {notice.detail || (disabledByPolicy
                ? "视频基础数据已完成；外部模型尚未通过生产就绪闸门，本轮不会调用。"
                : "当前暂无分析与时间戳；需要时可点击分析按钮提交任务。")}
            </div>
          </div>
        </div>
      );
    }
    return (
      <div className="mt-2 space-y-2">
        {overview}
        <div className="rounded-md border border-cyan-300/15 bg-cyan-400/[0.06] px-3 py-2" role="status">
          <div className="flex items-center gap-2 text-[10.5px] font-medium text-cyan-100">
            {notice.state === "pending" ? <Loader2 size={12} className="animate-spin" /> : <Clock3 size={12} />}
            {notice.state === "paused" ? "后台分析仍在继续" : "AI 深析与时间戳正在后台生成"}
          </div>
          <div className="mt-1 text-[9.5px] leading-relaxed text-slate-400">
            {notice.detail || notice.reason || "视频基础数据已可查看；分镜、评分和时间戳完成后会自动补到这里。"}
            {(notice.provider || notice.stage) ? ` · ${[notice.provider, notice.stage].filter(Boolean).join(" / ")}` : ""}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="mt-2 space-y-2">
      {overview}
      {evaluationOnly ? (
        <div
          className="flex items-center gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2.5 py-2 text-[10px] font-medium text-amber-100"
          data-testid="video-analysis-evaluation-notice"
          role="note"
        >
          <AlertTriangle size={11} className="shrink-0" />
          本地评估 · 描述性结论 · 非生产授权
        </div>
      ) : null}
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
