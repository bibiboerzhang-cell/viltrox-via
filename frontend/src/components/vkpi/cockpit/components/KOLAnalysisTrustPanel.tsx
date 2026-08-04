import { AlertTriangle, CheckCircle2, CircleDashed, ShieldCheck } from "lucide-react";

type Row = Record<string, unknown>;

function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function firstRecord(...values: unknown[]): Row {
  for (const value of values) {
    const record = asRecord(value);
    if (Object.keys(record).length) return record;
  }
  return {};
}

function text(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "";
}

function finiteNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (value == null || value === "") continue;
    const number = Number(value);
    if (Number.isFinite(number) && number >= 0) return number;
  }
  return null;
}

function ratioValue(...values: unknown[]): number | null {
  const value = finiteNumber(...values);
  if (value == null) return null;
  if (value > 1 && value <= 100) return value / 100;
  return Math.min(1, value);
}

function boolValue(...values: unknown[]): boolean | null {
  for (const value of values) {
    if (typeof value === "boolean") return value;
    const normalized = text(value).toLowerCase();
    if (["true", "yes", "1"].includes(normalized)) return true;
    if (["false", "no", "0"].includes(normalized)) return false;
  }
  return null;
}

function gapRows(...values: unknown[]): Array<{ code: string; severity: string; message: string }> {
  const rows: Array<{ code: string; severity: string; message: string }> = [];
  values.forEach((value) => {
    if (!Array.isArray(value)) return;
    value.forEach((raw, index) => {
      if (typeof raw === "string") {
        const message = raw.trim();
        if (message) rows.push({ code: message, severity: "", message });
        return;
      }
      const record = asRecord(raw);
      const code = text(record.code ?? record.key) || `gap_${index + 1}`;
      const severity = text(record.severity ?? record.level).toLowerCase();
      const message = text(record.message ?? record.reason ?? record.label ?? record.detail) || code.replace(/_/g, " ");
      rows.push({ code, severity, message });
    });
  });
  return rows.filter((row, index) => rows.findIndex((other) => other.code === row.code && other.message === row.message) === index);
}

function claimStatusLabel(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "descriptive_only") return "仅描述性";
  if (["decision_support", "advisory_only"].includes(normalized)) return "仅供决策辅助";
  if (["not_claimable", "abstain", "insufficient_evidence"].includes(normalized)) return "不可形成结论";
  if (["decision_ready", "evidence_backed"].includes(normalized)) return "服务端声明就绪";
  return value || "未声明";
}

function readinessPresentation(level: string, status: string, abstain: boolean | null) {
  const token = `${level} ${status}`.toLowerCase();
  if (abstain === true || /(blocked|insufficient|not_ready|not-ready|missing|low|abstain)/.test(token)) {
    return { label: "暂不就绪", tone: "blocked" as const };
  }
  if (/(partial|limited|medium|review|conditional|provisional)/.test(token)) {
    return { label: "证据有限", tone: "limited" as const };
  }
  if (/(decision_ready|production_ready|ready|sufficient|high)/.test(token)) {
    return { label: "可用于初步判断", tone: "ready" as const };
  }
  return { label: "统一口径待补", tone: "unknown" as const };
}

function scopePresentation(value: Row): { label: string; tone: "ready" | "limited" | "blocked" | "unknown"; raw: string } {
  const raw = [
    text(value.level),
    text(value.status),
    text(value.recommendation_status),
    text(value.decision_mode),
  ].filter(Boolean).join(" · ");
  const explicitAbstain = boolValue(value.abstain) === true || /(abstain|do_not_recommend|no_conclusion)/i.test(raw);
  const presentation = readinessPresentation(text(value.level), raw, explicitAbstain);
  return {
    label: presentation.tone === "ready"
      ? "可参考"
      : presentation.tone === "limited"
        ? "需复核"
        : presentation.tone === "blocked"
          ? "暂不判断"
          : "待补口径",
    tone: presentation.tone,
    raw,
  };
}

export interface AnalysisTrustViewModel {
  hasContract: boolean;
  readinessLabel: string;
  readinessRaw: string;
  tone: "ready" | "limited" | "blocked" | "unknown";
  claimStatus: string;
  evidenceReady: number | null;
  evidenceTotal: number | null;
  evidenceRatio: number | null;
  keySampleCount: number | null;
  qaReady: number | null;
  fullVideoProven: number | boolean | null;
  fullVideoRatio: number | null;
  gaps: Array<{ code: string; severity: string; message: string }>;
  abstain: boolean;
  scopes: Array<{
    key: "overall" | "content_fit" | "brand_history";
    label: string;
    status: string;
    tone: "ready" | "limited" | "blocked" | "unknown";
    raw: string;
  }>;
}

export function buildAnalysisTrustViewModel({
  detailBundle,
  item,
  videoAnalysisSummary,
}: {
  detailBundle?: unknown;
  item?: unknown;
  videoAnalysisSummary?: unknown;
}): AnalysisTrustViewModel {
  const bundle = asRecord(detailBundle);
  const bundleItem = asRecord(bundle.item);
  const itemRecord = asRecord(item);
  const diagnostics = asRecord(bundle.diagnostics);
  const videoAnalysis = asRecord(bundle.video_analysis);
  const summary = firstRecord(videoAnalysisSummary, videoAnalysis.summary);
  const readiness = firstRecord(
    bundle.analysis_readiness,
    bundleItem.analysis_readiness,
    itemRecord.analysis_readiness,
    diagnostics.analysis_readiness,
    summary.analysis_readiness,
  );
  const evidenceQuality = firstRecord(
    bundle.evidence_quality,
    bundleItem.evidence_quality,
    itemRecord.evidence_quality,
    diagnostics.evidence_quality,
    summary.evidence_quality,
  );
  const scopeRecords = firstRecord(readiness.scopes, evidenceQuality.scopes);
  // overall 是整位 KOL 的可用性；内容契合和品牌历史是局部作用域，不能互相冒充。
  const overallScope = firstRecord(scopeRecords.overall, readiness.overall, evidenceQuality.overall);
  const readinessBasis = Object.keys(overallScope).length ? overallScope : firstRecord(readiness, evidenceQuality);
  const coverage = firstRecord(
    readinessBasis.evidence_coverage,
    readiness.evidence_coverage,
    evidenceQuality.evidence_coverage,
    evidenceQuality.coverage,
  );
  const hasContract = Object.keys(readiness).length > 0 || Object.keys(evidenceQuality).length > 0;
  const level = text(readinessBasis.level ?? readiness.level ?? evidenceQuality.level);
  const recommendationStatus = text(readinessBasis.recommendation_status ?? readiness.recommendation_status ?? evidenceQuality.recommendation_status);
  const decisionMode = text(readinessBasis.decision_mode ?? readiness.decision_mode ?? evidenceQuality.decision_mode);
  const status = [
    text(readinessBasis.status ?? readiness.status ?? evidenceQuality.status),
    recommendationStatus,
    decisionMode,
  ].filter(Boolean).join(" · ");
  const abstainValue = boolValue(
    readinessBasis.abstain,
    readiness.abstain,
    evidenceQuality.abstain,
    readinessBasis.conclusion_recommended === false ? true : null,
    readinessBasis.can_conclude === false ? true : null,
    /(abstain|do_not_recommend|no_conclusion)/i.test(`${recommendationStatus} ${decisionMode}`) ? true : null,
  );
  const presentation = readinessPresentation(level, status, abstainValue);
  const evidenceTotal = finiteNumber(
    coverage.video_total,
    coverage.total,
    evidenceQuality.evidence_count,
    evidenceQuality.total_count,
    summary.evidence_count,
  );
  const evidenceReady = finiteNumber(
    coverage.deep_ready,
    coverage.ready,
    evidenceQuality.ready_count,
    evidenceQuality.analyzed_count,
    summary.ready_count,
  );
  const explicitRatio = ratioValue(
    coverage.deep_ratio,
    coverage.ratio,
    evidenceQuality.coverage_ratio,
    evidenceQuality.coverage_pct,
  );
  const evidenceRatio = explicitRatio ?? (
    evidenceTotal != null && evidenceTotal > 0 && evidenceReady != null
      ? Math.min(1, evidenceReady / evidenceTotal)
      : null
  );
  const keySampleCount = finiteNumber(
    readinessBasis.key_sample_count,
    readiness.key_sample_count,
    evidenceQuality.key_sample_count,
    evidenceQuality.critical_sample_count,
  );
  const qaReady = finiteNumber(coverage.qa_ready, evidenceQuality.qa_ready_count, summary.qa_ready_count);
  const fullVideoRaw = coverage.full_video_proven;
  const fullVideoProven = typeof fullVideoRaw === "boolean"
    ? fullVideoRaw
    : finiteNumber(fullVideoRaw, evidenceQuality.full_video_proven);
  const fullVideoRatio = ratioValue(coverage.full_video_ratio, evidenceQuality.full_video_ratio);
  const gaps = gapRows(
    readinessBasis.blocking_gaps,
    readinessBasis.blockers,
    readinessBasis.gaps,
    Object.keys(overallScope).length ? null : readiness.blocking_gaps,
    Object.keys(overallScope).length ? null : readiness.blockers,
    Object.keys(overallScope).length ? null : readiness.gaps,
    evidenceQuality.blocking_gaps,
    evidenceQuality.blockers,
    evidenceQuality.gaps,
  );
  const claimStatus = text(
    readinessBasis.claim_status
      ?? readiness.claim_status
      ?? evidenceQuality.claim_status
      ?? bundle.claim_status
      ?? bundleItem.claim_status
      ?? itemRecord.claim_status,
  );
  const noEvidence = evidenceTotal === 0;
  const hasBlockingGap = gaps.some((gap) => !gap.severity || /(block|critical|high|error)/.test(gap.severity));
  const abstain = abstainValue === true || presentation.tone === "blocked" || noEvidence || hasBlockingGap;
  const scopes = ([
    ["overall", "整体", Object.keys(overallScope).length ? overallScope : readinessBasis],
    ["content_fit", "内容契合", scopeRecords.content_fit],
    ["brand_history", "品牌历史", scopeRecords.brand_history],
  ] as Array<["overall" | "content_fit" | "brand_history", string, unknown]>).map(([key, label, value]) => {
    const scoped = scopePresentation(asRecord(value));
    return { key, label, status: scoped.label, tone: scoped.tone, raw: scoped.raw };
  });

  return {
    hasContract,
    readinessLabel: presentation.label,
    readinessRaw: [level, status].filter(Boolean).join(" · "),
    tone: presentation.tone,
    claimStatus: claimStatusLabel(claimStatus),
    evidenceReady,
    evidenceTotal,
    evidenceRatio,
    keySampleCount,
    qaReady,
    fullVideoProven,
    fullVideoRatio,
    gaps,
    abstain,
    scopes,
  };
}

function percent(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function coverageLabel(view: AnalysisTrustViewModel): string {
  if (view.evidenceReady != null && view.evidenceTotal != null) {
    return `${view.evidenceReady}/${view.evidenceTotal}${view.evidenceRatio != null ? ` · ${percent(view.evidenceRatio)}` : ""}`;
  }
  return percent(view.evidenceRatio);
}

function fullVideoLabel(view: AnalysisTrustViewModel): string {
  const proven = typeof view.fullVideoProven === "boolean"
    ? (view.fullVideoProven ? "已提供" : "未提供")
    : view.fullVideoProven != null
      ? `${view.fullVideoProven} 条`
      : "未声明";
  return `${proven}${view.fullVideoRatio != null ? ` · ${percent(view.fullVideoRatio)}` : ""}`;
}

export function KOLAnalysisTrustPanel({
  detailBundle,
  item,
  videoAnalysisSummary,
}: {
  detailBundle?: unknown;
  item?: unknown;
  videoAnalysisSummary?: unknown;
}) {
  const view = buildAnalysisTrustViewModel({ detailBundle, item, videoAnalysisSummary });
  const toneClass = view.tone === "ready"
    ? "border-emerald-300/25 bg-emerald-400/[0.08] text-emerald-100"
    : view.tone === "blocked"
      ? "border-rose-300/25 bg-rose-400/[0.08] text-rose-100"
      : view.tone === "limited"
        ? "border-amber-300/25 bg-amber-400/[0.08] text-amber-100"
        : "border-white/[0.08] bg-white/[0.025] text-slate-300";
  const StatusIcon = view.tone === "ready" ? CheckCircle2 : view.tone === "unknown" ? CircleDashed : AlertTriangle;

  return (
    <section className="border-b border-white/[0.06] px-5 py-3" data-testid="kol-analysis-trust-panel">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-[10.5px] font-semibold text-slate-200">
            <ShieldCheck size={12} className="text-cyan-300" /> 分析可信度
          </div>
          <div className="mt-0.5 text-[8.5px] text-slate-500">先看证据是否足够，再看模型结论</div>
        </div>
        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[9px] font-medium ${toneClass}`} title={view.readinessRaw || undefined}>
          <StatusIcon size={10} /> {view.readinessLabel}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {[
          ["证据覆盖", coverageLabel(view), "已深析 / 已收集视频"],
          ["关键样本", view.keySampleCount != null ? `${view.keySampleCount} 条` : "未声明", "用于关键判断的样本"],
          ["全片凭证", fullVideoLabel(view), "final_v1 不自动等于全片"],
          ["声明边界", view.claimStatus, "结论可使用到什么程度"],
        ].map(([label, value, note]) => (
          <div key={label} className="rounded-md border border-white/[0.055] bg-black/20 px-2 py-2">
            <div className="text-[8px] text-slate-600">{label}</div>
            <div className="mt-0.5 text-[10.5px] font-medium tabular-nums text-slate-200">{value}</div>
            <div className="mt-0.5 text-[7.5px] leading-snug text-slate-700">{note}</div>
          </div>
        ))}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1.5" data-testid="analysis-scope-statuses">
        <span className="mr-0.5 text-[8.5px] text-slate-600">作用域</span>
        {view.scopes.map((scope) => {
          const cls = scope.tone === "ready"
            ? "border-emerald-300/20 bg-emerald-400/[0.055] text-emerald-100/85"
            : scope.tone === "blocked"
              ? "border-rose-300/20 bg-rose-400/[0.055] text-rose-100/85"
              : scope.tone === "limited"
                ? "border-amber-300/20 bg-amber-400/[0.055] text-amber-100/85"
                : "border-white/[0.065] bg-black/15 text-slate-500";
          return (
            <span key={scope.key} className={`rounded border px-1.5 py-0.5 text-[8.5px] ${cls}`} title={scope.raw || undefined}>
              {scope.label} · {scope.status}
            </span>
          );
        })}
      </div>

      {view.qaReady != null ? (
        <div className="mt-1.5 text-[8.5px] text-slate-500">关键帧 QA 就绪 {view.qaReady} 条</div>
      ) : null}

      {!view.hasContract ? (
        <div className="mt-2 rounded-md border border-white/[0.07] bg-black/15 px-2.5 py-2 text-[9.5px] leading-relaxed text-slate-400" role="note">
          当前服务仍按旧合同返回：只能看到已有证据数量，尚不能确认统一就绪级别、关键样本和声明边界。
        </div>
      ) : view.abstain ? (
        <div className="mt-2 rounded-md border border-rose-300/20 bg-rose-400/[0.065] px-2.5 py-2 text-[9.5px] leading-relaxed text-rose-100" role="alert" data-testid="analysis-abstain-notice">
          <div className="font-semibold">暂不建议下结论</div>
          <div className="mt-0.5 text-rose-100/75">先补齐阻断证据或关键样本，再判断合作适配、品牌风险或投放价值。</div>
        </div>
      ) : view.tone === "ready" ? (
        <div className="mt-2 rounded-md border border-emerald-300/18 bg-emerald-400/[0.05] px-2.5 py-2 text-[9.5px] leading-relaxed text-emerald-100/85" role="note">
          可用于初步判断；仍需人工复核原始证据，不能替代合作事实和业务结果验证。
        </div>
      ) : (
        <div className="mt-2 rounded-md border border-amber-300/18 bg-amber-400/[0.05] px-2.5 py-2 text-[9.5px] leading-relaxed text-amber-100/85" role="note">
          当前仅可作描述性参考；请先复核警告项和原始证据，再决定是否进入合作判断。
        </div>
      )}

      {view.gaps.length ? (
        <div className="mt-2 space-y-1" data-testid="analysis-blocking-gaps">
          {view.gaps.slice(0, 4).map((gap) => (
            <div key={`${gap.code}-${gap.message}`} className="flex items-start gap-1.5 text-[9px] leading-relaxed text-amber-100/80">
              <AlertTriangle size={9} className="mt-0.5 shrink-0" />
              <span>{gap.message}<span className="ml-1 text-slate-600">{gap.code}</span></span>
            </div>
          ))}
          {view.gaps.length > 4 ? <div className="text-[8.5px] text-slate-600">另有 {view.gaps.length - 4} 项缺口</div> : null}
        </div>
      ) : null}

      <div className="mt-2 border-t border-white/[0.04] pt-1.5 text-[8.5px] leading-relaxed text-slate-600">
        证据覆盖只表示已分析材料占比，不是预测准确率，也不代表真实投放、销售或合作结果。
      </div>
    </section>
  );
}
