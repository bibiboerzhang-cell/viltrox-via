import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldCheck, Video } from "lucide-react";
import { getKolVideoAnalysisCache, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";

type VideoEvidence = Record<string, unknown>;

interface AnalysisBundle {
  video: VideoEvidence;
  finalEntry: VkpiKolVideoAnalysisCacheEntry | null;
  qaEntry: VkpiKolVideoAnalysisCacheEntry | null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function textFrom(value: unknown): string {
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

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = textFrom(value);
    if (text) return text;
  }
  return "";
}

function compactText(value: string, max = 150) {
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

function normaliseScore(value: unknown, fallback?: unknown) {
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

function analysisScoreColor(score: number | null) {
  if (score == null) return "#94a3b8";
  if (score >= 80) return "#34d399";
  if (score >= 60) return "#facc15";
  return "#fb7185";
}

function finalV1Payload(entry?: VkpiKolVideoAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  return asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
}

function finalV1QaPayload(entry?: VkpiKolVideoAnalysisCacheEntry | null) {
  const result = asRecord(entry?.result);
  const direct = asRecord(result.final_v1_keyframe_qa);
  if (Object.keys(direct).length) return direct;
  const nested = asRecord(asRecord(result.video_analysis_final_v1_keyframe_qa).final_v1_keyframe_qa);
  if (Object.keys(nested).length) return nested;
  if ("qa_pass" in result || "checks" in result || "issues" in result || "score_correction" in result) return result;
  return {};
}

function qaBoolean(value: unknown) {
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

function qaStatusLabel(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === "pass") return "通过";
  if (status === "warn") return "提醒";
  if (status === "fail") return "异常";
  if (status === "unknown") return "未知";
  return status || "未知";
}

function qaStatusClass(value: unknown) {
  const status = textFrom(value).toLowerCase();
  if (status === "pass") return "border-emerald-400/15 bg-emerald-500/10 text-emerald-200";
  if (status === "fail") return "border-rose-400/20 bg-rose-500/12 text-rose-200";
  if (status === "warn") return "border-amber-400/20 bg-amber-500/10 text-amber-200";
  return "border-white/[0.06] bg-slate-500/10 text-slate-300";
}

function qaCheckTags(checks: unknown) {
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

function qaIssueItems(value: unknown) {
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

function qaScoreCorrectionText(value: unknown) {
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

function AnalysisCard({ bundle }: { bundle: AnalysisBundle }) {
  const payload = finalV1Payload(bundle.finalEntry);
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
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
          <div className="truncate text-[11px] font-semibold text-white">{videoTitle(bundle.video)}</div>
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

export function KOLVideoAnalysisPanel({ apiToken, videos }: { apiToken?: string; videos: VideoEvidence[] }) {
  const evidenceVideos = useMemo(() => videos.filter((video) => videoEvidenceId(video)).slice(0, 3), [videos]);
  const [bundles, setBundles] = useState<AnalysisBundle[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    setBundles([]);
    if (!apiToken || !evidenceVideos.length) {
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    Promise.all(
      evidenceVideos.map(async (video) => {
        const evidenceId = videoEvidenceId(video);
        const [finalResult, qaResult] = await Promise.allSettled([
          getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1"),
          getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1_keyframe_qa"),
        ]);
        return {
          video,
          finalEntry: finalResult.status === "fulfilled" && finalResult.value.state === "ready" ? finalResult.value.entry || null : null,
          qaEntry: qaResult.status === "fulfilled" && qaResult.value.state === "ready" ? qaResult.value.entry || null : null,
        };
      }),
    )
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
  }, [apiToken, evidenceVideos]);

  const readyBundles = bundles.filter((bundle) => bundle.finalEntry);

  return (
    <section className="border-b border-white/[0.06] px-5 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Video size={11} className="text-cyan-400" />
          <span className="text-[10px] uppercase tracking-wider text-slate-500">视频深析结果</span>
        </div>
        <span className="text-[8.5px] text-slate-600">vkpi_analysis_cache</span>
      </div>

      {!apiToken ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-500">登录后读取视频深析缓存。</div>
      ) : !evidenceVideos.length ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-500">暂无 video evidence，无法匹配 final_v1 / QA 缓存。</div>
      ) : loading ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3 text-[10.5px] text-slate-400">读取 final_v1 / 关键帧 QA 缓存...</div>
      ) : readyBundles.length ? (
        <div className="space-y-2">
          {readyBundles.map((bundle) => (
            <AnalysisCard key={videoEvidenceId(bundle.video)} bundle={bundle} />
          ))}
        </div>
      ) : (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.012] p-3">
          <div className="flex items-center gap-1.5 text-[10.5px] text-slate-400">
            <CheckCircle2 size={11} className="text-slate-500" />
            暂无深度分析
          </div>
          <div className="mt-1 text-[9.5px] text-slate-600">
            已找到 {evidenceVideos.length} 条 video evidence，但未命中 video_analysis_final_v1 cache。
          </div>
          {error ? <div className="mt-1 text-[9.5px] text-rose-300">{error}</div> : null}
        </div>
      )}
    </section>
  );
}
