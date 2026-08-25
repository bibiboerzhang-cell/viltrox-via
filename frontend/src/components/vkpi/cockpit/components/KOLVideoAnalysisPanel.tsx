import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck, Video } from "lucide-react";
import { getKolVideoAnalysisBatch, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";
import { failureReasonHumanOf, hasReadableFailure } from "../../../../services/vkpi/failureReason";
import { FailureGuidance } from "../lib/failureGuidance";
// 【K4】媒体种类徽章(图/组图/视频):helpers 只从本文件 import 类型(编译期擦除),无运行时环。
import { mediaKindBadge } from "./KOLDetailDrawer.helpers";

type VideoEvidence = Record<string, unknown>;

export interface AnalysisBundle {
  video: VideoEvidence;
  finalEntry: VkpiKolVideoAnalysisCacheEntry | null;
  qaEntry: VkpiKolVideoAnalysisCacheEntry | null;
  state?: string;
  reason?: string;
  analysisJob?: Record<string, unknown> | null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function textFrom(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.map(textFrom).filter(Boolean).join(" / ");
  const record = asRecord(value);
  for (const key of ["rationale", "evaluation", "summary", "text", "reason", "value", "evidence", "flag"]) {
    if (!(key in record)) continue;
    const text = textFrom(record[key]);
    if (text) return text;
  }
  return "";
}

export function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return "";
}

export function compactText(value: string, max = 150) {
  if (!value) return "暂无明确结论";
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function formatLargeNum(value: unknown) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) return "—";
  if (numeric >= 1000000) return `${(numeric / 1000000).toFixed(1)}M`;
  if (numeric >= 1000) return `${(numeric / 1000).toFixed(1)}K`;
  return String(Math.round(numeric));
}

export function normaliseScore(value: unknown, fallback?: unknown) {
  const source = value ?? fallback;
  if (typeof source === "number" && Number.isFinite(source)) return { score: Math.round(source), rationale: "", confidence: null as number | null };
  const record = asRecord(source);
  const rawScore = Number(record.score ?? record.value);
  const rawConfidence = Number(record.confidence);
  return {
    score: Number.isFinite(rawScore) ? Math.round(rawScore) : null,
    rationale: textFrom(record.rationale ?? record.evaluation ?? record.reason),
    confidence: Number.isFinite(rawConfidence) ? rawConfidence : null,
  };
}

export function analysisScoreColor(score: number | null) {
  if (score == null) return "#94a3b8";
  if (score >= 80) return "#34d399";
  if (score >= 60) return "#facc15";
  return "#fb7185";
}

function finalV1Payload(entry?: VkpiKolVideoAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  return asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
}

function sceneTimelineRows(value: unknown, max = 8) {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    return {
      key: `${textFrom(record.timestamp) || "scene"}-${index}`,
      timestamp: textFrom(record.timestamp),
      what: textFrom(record.what ?? record.scene ?? record.content),
    };
  }).filter((row) => row.timestamp || row.what).slice(0, max);
}

export function finalV1QaPayload(entry?: VkpiKolVideoAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  const direct = asRecord(result.final_v1_keyframe_qa);
  if (Object.keys(direct).length) return direct;
  const nested = asRecord(asRecord(result.video_analysis_final_v1_keyframe_qa).final_v1_keyframe_qa);
  if (Object.keys(nested).length) return nested;
  if ("qa_pass" in result || "checks" in result || "issues" in result || "score_correction" in result) return result;
  return {};
}

export function qaBoolean(value: unknown) {
  if (typeof value === "boolean") return value;
  const text = String(value ?? "").trim().toLowerCase();
  if (["true", "1", "yes", "pass", "passed"].includes(text)) return true;
  if (["false", "0", "no", "fail", "failed"].includes(text)) return false;
  return null;
}

const QA_CHECK_LABELS: Record<string, string> = {
  product_identity: "型号",
  brand_exposure: "品牌",
  competitor_context: "竞品",
  inscription_or_model_text: "铭文",
  title_image_consistency: "标题画面",
};

export function qaStatusLabel(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === "pass") return "通过";
  if (status === "warn") return "提醒";
  if (status === "fail") return "异常";
  if (status === "unknown") return "未知";
  return status || "未知";
}

export function qaStatusClass(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === "pass") return "border-emerald-400/15 bg-emerald-500/10 text-emerald-200";
  if (status === "fail") return "border-rose-400/20 bg-rose-500/12 text-rose-200";
  if (status === "warn") return "border-amber-400/20 bg-amber-500/10 text-amber-200";
  return "border-white/[0.06] bg-slate-500/10 text-slate-300";
}

export function qaCheckTags(checks: unknown) {
  return Object.entries(asRecord(checks)).map(([key, value]) => {
    const record = asRecord(value);
    return {
      key,
      label: QA_CHECK_LABELS[key] || key.replace(/_/g, " "),
      status: textFrom(record.status) || "unknown",
      detail: firstText(record.issues, record.evidence, record.observed_products, record.observed_text, record.observed_brand_signals, record.observed_competitors),
    };
  });
}

export function qaIssueItems(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    const type = textFrom(record.type) || `issue_${index + 1}`;
    const timestamp = textFrom(record.timestamp);
    return {
      key: `${timestamp || type}-${index}`,
      label: [timestamp, type.replace(/_/g, " ")].filter(Boolean).join(" · ") || type,
      evidence: textFrom(record.evidence),
      correction: textFrom(record.correction),
    };
  }).filter((item) => item.label || item.evidence || item.correction);
}

export function qaScoreCorrectionText(value: unknown) {
  const correction = asRecord(value);
  if (!Object.keys(correction).length) return "";
  const apply = qaBoolean(correction.apply);
  const delta = Number(correction.marketing_value_delta);
  const corrected = Number(correction.corrected_marketing_value_score);
  const rationale = textFrom(correction.rationale);
  const parts: string[] = [apply ? "建议纠偏" : "不建议调分"];
  if (Number.isFinite(delta) && delta !== 0) parts.push(`营销分 ${delta > 0 ? "+" : ""}${delta}`);
  if (Number.isFinite(corrected)) parts.push(`纠偏后 ${Math.round(corrected)}`);
  if (rationale) parts.push(rationale);
  return parts.join(" · ");
}

function videoEvidenceId(video: VideoEvidence) {
  const raw = video.evidence_id ?? video.id;
  const text = String(raw ?? "").trim();
  return text || "";
}

function videoTitle(video: VideoEvidence) {
  return textFrom(video.title ?? video.video_title ?? video.content_url) || "未命名视频";
}

function videoUrl(video: VideoEvidence) {
  return textFrom(video.url ?? video.content_url);
}

function analysisBundleState(bundle: AnalysisBundle) {
  return textFrom(bundle.state || bundle.finalEntry?.status).toLowerCase();
}

function isReadyAnalysisBundle(bundle: AnalysisBundle) {
  return analysisBundleState(bundle) === "ready" && Boolean(bundle.finalEntry);
}

function ScoreBlock({ label, score }: { label: string; score: ReturnType<typeof normaliseScore> }) {
  return (
    <div className="rounded-md border border-white/[0.05] bg-black/25 px-2.5 py-2">
      <div className="mb-1 text-[9px] text-slate-500">{label}</div>
      <div className="text-[22px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(score.score) }}>
        {score.score ?? "—"}
      </div>
    </div>
  );
}

// item3:layer3_three_values(三观)/ layer4_attribution(归因)/ layer5_recommendations(建议)
// 此前 AnalysisCard 只画了 layer1/2/6,这三层全埋着。折叠展示,默认收起。缺字段即不渲染(不杜撰)。
function whyActionText(value: unknown): { why: string; action: string } {
  const r = asRecord(value);
  return { why: textFrom(r.why), action: firstText(r.action, typeof value === "string" ? value : "") };
}

export function DeepLayersSection({ layer3, layer4, layer5 }: { layer3: Record<string, unknown>; layer4: Record<string, unknown>; layer5: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);

  const threeValues = [
    { label: "素材价值", cell: asRecord(layer3.asset_value) },
    { label: "渠道价值", cell: asRecord(layer3.channel_value) },
    { label: "产品实证", cell: asRecord(layer3.product_proof_value) },
  ].filter((row) => Object.keys(row.cell).length);

  const attributionRisk = textFrom(layer4.attribution_risk);
  const breakdown = Array.isArray(layer4.attribution_breakdown) ? (layer4.attribution_breakdown as unknown[]).map(asRecord) : [];

  const recRows = [
    { label: "预算动作", cell: layer5.budget_action },
    { label: "须向 KOL 索取", cell: layer5.must_request_from_kol },
    { label: "下次 brief 调整", cell: layer5.next_brief_adjustments },
    { label: "合作建议", cell: layer5.cooperation_recommendation },
    { label: "素材买断/授权", cell: layer5.buyout_or_license_recommendation },
  ].map((row) => ({ label: row.label, ...whyActionText(row.cell) })).filter((row) => row.why || row.action);

  const hasAny = threeValues.length > 0 || Boolean(attributionRisk) || breakdown.length > 0 || recRows.length > 0;
  if (!hasAny) return null;

  return (
    <div className="mt-2 border-t border-white/[0.05] pt-2">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between text-[9.5px] text-cyan-200/80 hover:text-cyan-100">
        <span>三观 / 归因 / 建议</span>
        <span className="text-slate-600">{open ? "收起 ▲" : "展开 ▼"}</span>
      </button>
      {open ? (
        <div className="mt-2 space-y-2.5">
          {threeValues.length ? (
            <div>
              <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">三观(素材 / 渠道 / 产品实证)</div>
              <div className="space-y-1">
                {threeValues.map((row) => {
                  const s = normaliseScore(row.cell);
                  const evidence = textFrom(row.cell.evidence);
                  return (
                    <div key={row.label} className="rounded border border-white/[0.04] bg-black/15 px-2 py-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] text-slate-300">{row.label}</span>
                        <span className="text-[11px] font-bold tabular-nums" style={{ color: analysisScoreColor(s.score) }}>{s.score ?? "—"}</span>
                      </div>
                      {evidence ? <div className="mt-0.5 text-[9px] leading-relaxed text-slate-500">{compactText(evidence, 220)}</div> : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {attributionRisk || breakdown.length ? (
            <div>
              <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">归因(效果由谁贡献)</div>
              {attributionRisk ? (
                <div className="mb-1 rounded border border-amber-300/15 bg-amber-300/[0.04] px-2 py-1 text-[9.5px] leading-relaxed text-slate-300">
                  <span className="text-amber-200">风险:</span> {compactText(attributionRisk, 200)}
                </div>
              ) : null}
              {breakdown.length ? (
                <div className="space-y-1">
                  {breakdown.slice(0, 5).map((row, i) => {
                    const pct = Number(row.share_pct_estimate);
                    const evidence = textFrom(row.evidence);
                    return (
                      <div key={i} className="rounded border border-white/[0.04] bg-black/15 px-2 py-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[10px] text-slate-300">{compactText(textFrom(row.source), 60)}</span>
                          {Number.isFinite(pct) ? <span className="shrink-0 text-[10px] font-semibold tabular-nums text-cyan-200">{pct}%</span> : null}
                        </div>
                        {evidence ? <div className="mt-0.5 text-[9px] leading-relaxed text-slate-500">{compactText(evidence, 180)}</div> : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}

          {recRows.length ? (
            <div>
              <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">建议</div>
              <div className="space-y-1">
                {recRows.map((row) => (
                  <div key={row.label} className="rounded border border-white/[0.05] bg-black/20 px-2 py-1">
                    <div className="mb-0.5 text-[9px] text-cyan-200">{row.label}</div>
                    {row.action ? <div className="text-[10px] leading-relaxed text-slate-300">{compactText(row.action, 220)}</div> : null}
                    {row.why ? <div className="mt-0.5 text-[9px] leading-relaxed text-slate-500">因为: {compactText(row.why, 160)}</div> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function AnalysisCard({ bundle }: { bundle: AnalysisBundle }) {
  const payload = finalV1Payload(bundle.finalEntry);
  const layer1 = asRecord(payload.layer1_visual_content);
  const contentSummary = textFrom(layer1.content_summary);
  const sceneTimeline = sceneTimelineRows(layer1.scene_timeline);
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer4 = asRecord(payload.layer4_attribution);
  const layer5 = asRecord(payload.layer5_recommendations);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const channelValue = normaliseScore(scores.channel_value_score);
  const assetValue = normaliseScore(scores.asset_reuse_score);
  const productProof = normaliseScore(scores.product_proof_score);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || textFrom(layer6.key_hook);
  const riskText = textFrom(layer6.risk_flags);
  const qaPayload = finalV1QaPayload(bundle.qaEntry);
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaPass = qaBoolean(qaPayload.qa_pass ?? asRecord(bundle.qaEntry?.result).qa_pass);
  const qaBadgeText = qaPass === false ? "需复核" : qaPass === true ? "通过" : "未定";
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  const url = videoUrl(bundle.video);

  return (
    <div className="rounded-lg border border-cyan-400/15 bg-cyan-400/[0.035] p-3">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <div className="truncate text-[11px] font-semibold text-white">{videoTitle(bundle.video)}</div>
            {/* 【K4】媒体种类徽章:evidence 带 media_kind/evidence_type 才显示,缺失静默 */}
            {(() => {
              const badge = mediaKindBadge(bundle.video);
              return badge ? (
                <span className="shrink-0 rounded border border-cyan-300/25 bg-cyan-400/[0.08] px-1 py-px text-[8.5px] font-medium text-cyan-100/90" title={badge.title}>
                  {badge.label}
                </span>
              ) : null;
            })()}
          </div>
          <div className="mt-0.5 text-[9.5px] text-slate-500">
            evidence #{videoEvidenceId(bundle.video)} · 播放 {formatLargeNum(bundle.video.view_count ?? bundle.video.views)}
          </div>
        </div>
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" className="shrink-0 text-[10px] text-cyan-300">
            打开
          </a>
        ) : null}
      </div>

      <div className="mb-2 grid grid-cols-2 gap-2">
        <ScoreBlock label="内容质量" score={contentScore} />
        <ScoreBlock label="投放价值" score={marketingScore} />
      </div>
      <div className="mb-2 text-[10.5px] leading-relaxed text-slate-300">{compactText(verdict, 180)}</div>

      {contentSummary ? (
        <div className="mb-2 rounded-md border border-white/[0.05] bg-white/[0.02] px-2.5 py-2">
          <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">内容概述</div>
          <div className="text-[10.5px] leading-relaxed text-slate-300">{compactText(contentSummary, 200)}</div>
        </div>
      ) : null}

      {sceneTimeline.length ? (
        <div className="mb-2 rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
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

      <div className="mb-2 grid grid-cols-3 gap-1.5">
        {[
          ["渠道", channelValue],
          ["素材", assetValue],
          ["产品", productProof],
        ].map(([label, score]) => {
          const typedScore = score as ReturnType<typeof normaliseScore>;
          return (
            <div key={label as string} className="rounded-md border border-white/[0.05] bg-white/[0.025] px-2 py-1.5">
              <div className="text-[8.5px] text-slate-500">{label as string}</div>
              <div className="text-[14px] font-bold tabular-nums" style={{ color: analysisScoreColor(typedScore.score) }}>
                {typedScore.score ?? "—"}
              </div>
            </div>
          );
        })}
      </div>

      <div className="mb-2 flex flex-wrap gap-1.5 text-[9.5px]">
        {viewerReaction ? <span className="rounded bg-purple-500/10 px-2 py-1 text-purple-200">心动: {compactText(viewerReaction, 54)}</span> : null}
        {riskText ? <span className="rounded bg-amber-500/10 px-2 py-1 text-amber-200">风险: {compactText(riskText, 60)}</span> : null}
      </div>

      <DeepLayersSection layer3={layer3} layer4={layer4} layer5={layer5} />

      {qaHasPayload ? (
        <div className={`mt-2 rounded-md border p-2 ${qaPass === false ? "border-rose-400/20 bg-rose-500/[0.045]" : "border-emerald-400/15 bg-emerald-500/[0.035]"}`}>
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

export function KOLVideoAnalysisPanel({
  apiToken,
  videos,
  preloadedBundles,
  summary,
  crawling = false,
  analysisState,
  onRefresh,
}: {
  apiToken?: string;
  videos: VideoEvidence[];
  preloadedBundles?: AnalysisBundle[] | null;
  summary?: Record<string, unknown> | null;
  // 【A3】抓取进度徽标:父层(KOLDetailDrawer)在 profile 深爬入队/轮询中时置 true,
  // 标题旁显示「抓取中·完成自动出现」。纯 props 透传,本组件不新增任何全局依赖。
  crawling?: boolean;
  analysisState?: { status?: string; message?: string } | null;
  onRefresh?: () => void | Promise<void>;
}) {
  const evidenceVideos = useMemo(() => videos.filter((video) => videoEvidenceId(video)).slice(0, 3), [videos]);
  // Parent detail mapping can recreate equivalent arrays on every render. Depend on a
  // semantic signature below so the cache probe does not fan out again for the same IDs.
  const evidenceSignature = evidenceVideos.map((video) => videoEvidenceId(video)).join("|");
  const preloadedSignature = (Array.isArray(preloadedBundles) ? preloadedBundles : [])
    .map((bundle) => `${videoEvidenceId(bundle.video)}:${bundle.finalEntry ? "ready" : bundle.state || "missing"}`)
    .join("|");
  const summaryRecord = asRecord(summary);
  const summaryReadyCount = Number(summaryRecord.ready_count);
  const summaryEvidenceCount = Number(summaryRecord.evidence_count);
  const [bundles, setBundles] = useState<AnalysisBundle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showAll, setShowAll] = useState(false); // C2:76条长卡默认收起,只显前3条
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    if (!onRefresh || refreshing) return;
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    setError("");
    setBundles([]);
    const preloadedByEvidence = new Map(
      (Array.isArray(preloadedBundles) ? preloadedBundles : []).map((bundle) => [videoEvidenceId(bundle.video), bundle]),
    );
    const allPreloadedSettled = evidenceVideos.length > 0
      && evidenceVideos.every((video) => {
        const bundle = preloadedByEvidence.get(videoEvidenceId(video));
        return Boolean(bundle && (
          isReadyAnalysisBundle(bundle)
          || ["quality_incomplete", "stale", "legacy_unverified"].includes(analysisBundleState(bundle))
        ));
      });
    if (allPreloadedSettled) {
      setBundles(Array.isArray(preloadedBundles) ? preloadedBundles : []);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    if (!apiToken || !evidenceVideos.length) {
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    // C9(优化波 B):逐卡 2×N 次缓存探针改为 2 次批量矩阵(analysis-cache/batch,同一 entry/analysis_job 形状)。
    const evidenceIds = evidenceVideos.map((video) => videoEvidenceId(video));
    Promise.allSettled([
      getKolVideoAnalysisBatch(apiToken, evidenceIds, "video_analysis_final_v1"),
      getKolVideoAnalysisBatch(apiToken, evidenceIds, "video_analysis_final_v1_keyframe_qa"),
    ])
      .then(([finalBatch, qaBatch]) => {
        const indexBatch = (result: PromiseSettledResult<{ items?: unknown[] } | null | undefined>) => {
          const rows = result.status === "fulfilled" && Array.isArray(result.value?.items) ? result.value.items : [];
          return new Map(rows.map((row) => [textFrom(asRecord(row).target_id), asRecord(row)]));
        };
        const finalById = indexBatch(finalBatch);
        const qaById = indexBatch(qaBatch);
        return evidenceVideos.map((video) => {
          const evidenceId = textFrom(videoEvidenceId(video));
          const finalResponse = finalById.get(evidenceId) || null;
          const qaResponse = qaById.get(evidenceId) || null;
          const analysisJob = asRecord(finalResponse?.analysis_job);
          const state = textFrom(analysisJob.state ?? analysisJob.status ?? finalResponse?.state).toLowerCase() || "unknown";
          return {
            video,
            finalEntry: finalResponse?.state === "ready" ? (finalResponse.entry as VkpiKolVideoAnalysisCacheEntry) || null : null,
            qaEntry: state === "ready" && qaResponse?.state === "ready" ? (qaResponse.entry as VkpiKolVideoAnalysisCacheEntry) || null : null,
            state,
            // F3 失败可读:后端人话原因优先;旧机器码只在缺席时兜底。
            reason: failureReasonHumanOf(analysisJob) || firstText(analysisJob.reason_detail, analysisJob.reason, analysisJob.error_category, state),
            analysisJob: Object.keys(analysisJob).length ? analysisJob : null,
          };
        });
      })
      .then((items) => {
        if (!cancelled) setBundles(items);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "深度分析缓存读取失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, evidenceSignature, preloadedSignature]);

  const readyBundles = bundles.filter(isReadyAnalysisBundle);
  const requestedState = textFrom(analysisState?.status).toLowerCase();
  const liveBundle = bundles.find((bundle) => ["queued", "running", "retrying", "processing", "pending"].includes(String(bundle.state || "")));
  const terminalBundle = bundles.find((bundle) => ["blocked", "failed", "error", "cancelled", "canceled"].includes(String(bundle.state || "")));
  const qualityIncompleteBundle = bundles.find((bundle) => analysisBundleState(bundle) === "quality_incomplete");
  const staleBundle = bundles.find((bundle) => analysisBundleState(bundle) === "stale");
  const legacyBundle = bundles.find((bundle) => analysisBundleState(bundle) === "legacy_unverified");
  const analysisNotice: { kind: "active" | "blocked" | "idle" | "quality" | "stale" | "legacy"; text: string } | null = legacyBundle
    ? { kind: "legacy", text: "历史结果待核验；完成重新验证前不计入已分析。" }
    : qualityIncompleteBundle
    ? { kind: "quality", text: "结果质量未通过；待重试或人工复核。" }
    : staleBundle
      ? { kind: "stale", text: "历史分析已过期；请重新发起分析以生成当前结果。" }
    : analysisState?.message
    ? { kind: requestedState === "ai_disabled" || requestedState === "not_requested" ? "blocked" : "active", text: analysisState.message }
    : terminalBundle
      ? { kind: "blocked", text: `视频深析未完成：${terminalBundle.reason || terminalBundle.state || "任务终止"}` }
      : liveBundle?.analysisJob
        ? { kind: "active", text: `视频深析正在${liveBundle.state === "queued" ? "排队" : "处理"}，完成后自动回填。` }
        : bundles.some((bundle) => bundle.state === "not_requested")
          ? { kind: "idle", text: "尚未请求 final_v1 深析；可点击下方“AI深度分析”发起。" }
          : null;

  return (
    <section className="border-b border-white/[0.06] px-5 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Video size={11} className="text-cyan-400" />
          <span className="text-[10px] uppercase tracking-wider text-slate-500">视频深析结果</span>
          {crawling ? (
            <span className="rounded border border-amber-400/25 bg-amber-400/[0.10] px-1.5 py-0.5 text-[8.5px] font-medium text-amber-300">
              抓取中 · 完成自动出现
            </span>
          ) : null}
          {analysisNotice?.kind === "active" ? (
            <span className="rounded border border-cyan-400/25 bg-cyan-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-cyan-200">分析处理中</span>
          ) : analysisNotice?.kind === "quality" ? (
            <span className="rounded border border-rose-400/25 bg-rose-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-rose-200">结果质量未通过</span>
          ) : analysisNotice?.kind === "stale" ? (
            <span className="rounded border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-amber-200">历史分析已过期</span>
          ) : analysisNotice?.kind === "legacy" ? (
            <span className="rounded border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-amber-200">历史结果待核验</span>
          ) : analysisNotice?.kind === "blocked" ? (
            <span className="rounded border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[8.5px] font-medium text-amber-200">未入队/已阻断</span>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          {Number.isFinite(summaryReadyCount) && summaryReadyCount > 0 ? (
            <span className="rounded bg-cyan-500/12 px-1.5 py-0.5 text-[9px] font-medium text-cyan-200">已有 {summaryReadyCount} 条分析</span>
          ) : Number.isFinite(summaryEvidenceCount) && summaryEvidenceCount > 0 ? (
            <span className="rounded bg-slate-500/12 px-1.5 py-0.5 text-[9px] text-slate-300">已有 {summaryEvidenceCount} 条 evidence</span>
          ) : null}
          {onRefresh ? (
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={refreshing}
              aria-label="刷新视频分析状态"
              className="inline-flex items-center gap-1 rounded border border-white/[0.08] px-1.5 py-0.5 text-[8.5px] text-slate-400 hover:border-cyan-300/25 hover:text-cyan-100 disabled:opacity-50"
            >
              <RefreshCw size={9} className={refreshing ? "animate-spin" : ""} />
              {refreshing ? "刷新中" : "刷新状态"}
            </button>
          ) : null}
          <span className="text-[8.5px] text-slate-600">vkpi_analysis_cache</span>
        </div>
      </div>

      {!apiToken ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-500">登录后读取视频深析缓存。</div>
      ) : !evidenceVideos.length ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-500">暂无 video evidence，无法匹配 final_v1 / QA 缓存。</div>
      ) : loading ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-400">读取 final_v1 / 关键帧 QA 缓存...</div>
      ) : readyBundles.length ? (
        <div className="space-y-2">
          {analysisNotice?.kind === "quality" ? (
            <div className="rounded-md border border-rose-300/20 bg-rose-500/[0.07] px-3 py-2 text-[10px] text-rose-100" role="status">
              <div className="font-medium">有视频结果质量未通过</div>
              <div className="mt-0.5 text-[9.5px] text-rose-100/75">待重试或人工复核；该条不计入已分析结果。</div>
            </div>
          ) : null}
          {staleBundle ? (
            <div className="rounded-md border border-amber-300/20 bg-amber-500/[0.07] px-3 py-2 text-[10px] text-amber-100" role="status">
              <div className="font-medium">有历史分析已过期</div>
              <div className="mt-0.5 text-[9.5px] text-amber-100/75">请重新发起分析；过期条目不计入当前已分析结果。</div>
            </div>
          ) : null}
          {legacyBundle ? (
            <div className="rounded-md border border-amber-300/20 bg-amber-500/[0.07] px-3 py-2 text-[10px] text-amber-100" role="status">
              <div className="font-medium">历史结果待核验</div>
              <div className="mt-0.5 text-[9.5px] text-amber-100/75">原始结果已保留，但当前界面不将其作为已核验结论展示。</div>
            </div>
          ) : null}
          {(showAll ? readyBundles : readyBundles.slice(0, 3)).map((bundle) => (
            <AnalysisCard key={videoEvidenceId(bundle.video)} bundle={bundle} />
          ))}
          {readyBundles.length > 3 ? (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="w-full rounded-md border border-cyan-300/20 bg-cyan-400/[0.06] px-3 py-1.5 text-[10.5px] font-medium text-cyan-100 hover:bg-cyan-400/[0.12]"
            >
              {showAll ? "收起逐条分析" : `展开全部 ${readyBundles.length} 条逐视频分析 ▾`}
            </button>
          ) : null}
        </div>
      ) : (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3">
          <div className="flex items-center gap-1.5 text-[10.5px] text-slate-400">
            {analysisNotice?.kind === "blocked" || analysisNotice?.kind === "quality" || analysisNotice?.kind === "stale" || analysisNotice?.kind === "legacy" ? <AlertTriangle size={11} className={analysisNotice.kind === "quality" ? "text-rose-300" : "text-amber-300"} /> : <CheckCircle2 size={11} className="text-slate-500" />}
            {analysisNotice?.kind === "active" ? "深度分析处理中" : analysisNotice?.kind === "quality" ? "结果质量未通过" : analysisNotice?.kind === "legacy" ? "历史结果待核验" : analysisNotice?.kind === "stale" ? "历史分析已过期" : analysisNotice?.kind === "blocked" ? "深度分析未启动" : "暂无深度分析"}
          </div>
          <div className="mt-1 text-[9.5px] text-slate-600">
            {analysisNotice?.text || `已找到 ${Number.isFinite(summaryEvidenceCount) && summaryEvidenceCount > 0 ? summaryEvidenceCount : evidenceVideos.length} 条 video evidence，但尚无 video_analysis_final_v1 结果。`}
          </div>
          {/* F3 失败可读:终态项带 failure_category / failure_reason_human 才渲染;authorization 类跳 MY KOL 由负责人重发。 */}
          {terminalBundle?.analysisJob && hasReadableFailure(terminalBundle.analysisJob) ? (
            <FailureGuidance
              source={terminalBundle.analysisJob}
              onReissue={() => window.dispatchEvent(new CustomEvent("vkpi:open-mykol-kol", { detail: { evidenceId: videoEvidenceId(terminalBundle.video) } }))}
            />
          ) : null}
          {error ? <div className="mt-1 text-[9.5px] text-rose-300">{error}</div> : null}
        </div>
      )}
    </section>
  );
}
