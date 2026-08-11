// D 件 预测台账面板:「系统的预测有多准」—— 驾照升降级的数据引擎读数。
// 数据:GET /api/admin/vkpi/prediction-ledger/summary —— 纯 SQL 聚合真实表
// (推荐 outcome+反馈 / Market Brain 押注 / 告警闭环 / 品牌信号 / 执行台账),零 LLM。
// 每组:命中率条 + 样本徽章 + 置信度;样本<5 显著标注 insufficient(驾照不得据此升级)。
// 诚实态:数据荒是常态 —— 空组(sample_count=0)照实列出并给 pending/empty 原因,
// performance_forecast 有预测无 actual 永远「待对答案」,绝不编数;接口失败整块安静缺席。
// 红线:纯展示,台账永不影响任何评分(影响评分=NO,不触 fit 评分列/rule_v0)。
import React from "react";
import { Crosshair } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import {
  recordPredictionActual,
  type PredictionEvidenceField,
} from "../../../../services/vkpi/prediction-ledger-api";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type LedgerBasis = {
  hit_definition?: string;
  miss_definition?: string;
  pending_definition?: string;
  hits?: number;
  misses?: number;
  judged_total?: number;
  pending_count?: number;
  window?: number;
  note?: string;
  reason?: string;
  source?: string[];
};
type LedgerGroup = {
  action_type?: string;
  label?: string;
  status?: string; // ok | pending | empty | error | unknown_action_type
  hit_rate?: number | null;
  sample_count?: number;
  confidence?: string; // none | insufficient | low | medium | high
  basis?: LedgerBasis;
};
type LedgerSummaryResp = {
  status?: string;
  generated_at?: string;
  window?: number;
  groups?: LedgerGroup[];
  totals?: { groups?: number; judged_total?: number; pending_total?: number; groups_with_sample?: number };
  note?: string;
};

function fmtPct(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

// 命中率配色:高 emerald / 中 amber / 低 rose;无样本一律灰(不给空组上色装数据)。
function rateColor(rate: number | null | undefined, sample: number): { bar: string; text: string } {
  if (sample <= 0 || typeof rate !== "number") return { bar: "rgba(148,163,184,0.25)", text: "text-slate-500" };
  if (rate >= 0.7) return { bar: "linear-gradient(90deg, rgba(52,211,153,0.35), rgba(52,211,153,0.8))", text: "text-emerald-300" };
  if (rate >= 0.4) return { bar: "linear-gradient(90deg, rgba(251,191,36,0.35), rgba(251,191,36,0.8))", text: "text-amber-300" };
  return { bar: "linear-gradient(90deg, rgba(251,113,133,0.35), rgba(251,113,133,0.8))", text: "text-rose-300" };
}

const CONFIDENCE_LABEL: Record<string, string> = {
  none: "无样本",
  insufficient: "样本不足",
  low: "低置信",
  medium: "中置信",
  high: "高置信",
};

// 样本徽章:insufficient 用 amber 显著标注(驾照引擎据此绝不升级),其余按置信度着色。
function SampleBadge(group: LedgerGroup) {
  const sample = Number(group.sample_count) || 0;
  const conf = String(group.confidence || "none");
  const label = `样本 ${sample}` + (CONFIDENCE_LABEL[conf] ? ` · ${CONFIDENCE_LABEL[conf]}` : "");
  const cls =
    conf === "insufficient" || conf === "none"
      ? "bg-amber-500/10 text-amber-200 border border-amber-300/20"
      : conf === "high" || conf === "medium"
        ? "bg-emerald-500/10 text-emerald-200 border border-emerald-300/10"
        : "bg-slate-500/10 text-slate-300 border border-white/[0.06]";
  return e("span", { className: "shrink-0 rounded px-1.5 py-0.5 text-[8.5px] tabular-nums " + cls }, label);
}

// 单组行:命中率条 + 样本徽章;非 ok 组给诚实状态文案(pending/empty/error 各有说法)。
function GroupRow(group: LedgerGroup, i: number) {
  const sample = Number(group.sample_count) || 0;
  const rate = typeof group.hit_rate === "number" ? group.hit_rate : null;
  const status = String(group.status || "");
  const basis = group.basis || {};
  const colors = rateColor(rate, sample);
  const widthPct = sample > 0 && rate != null ? Math.max(3, Math.round(rate * 100)) : 0;
  const pendingCount = Number(basis.pending_count) || 0;
  let statusLine: string | null = null;
  if (status === "pending") {
    statusLine = String(basis.pending_definition || "待对答案:有预测无结果,暂无法计算命中率");
  } else if (status === "empty") {
    statusLine = "暂无该组数据(诚实空组,不编数)";
  } else if (status === "error") {
    statusLine = "该组聚合失败:" + String(basis.reason || "未知原因");
  }
  return e("div", { key: group.action_type || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-2" },
      e("span", {
        className: "w-[110px] shrink-0 truncate text-[10px] text-slate-300",
        title: `${group.label || group.action_type || ""}(${group.action_type || ""})`,
      }, group.label || group.action_type || "—"),
      e("div", {
        className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]",
        title: basis.hit_definition ? `命中定义:${basis.hit_definition}` : undefined,
      },
        widthPct > 0 && e("div", {
          className: "absolute inset-y-0 left-0 rounded-full",
          style: { width: widthPct + "%", background: colors.bar },
        }),
      ),
      e("span", { className: "w-[44px] shrink-0 text-right text-[10px] font-medium tabular-nums " + colors.text }, fmtPct(rate)),
      SampleBadge(group),
    ),
    e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[8.5px] tabular-nums text-slate-500" },
      sample > 0 && e("span", null, `命中 ${Number(basis.hits) || 0} / 未中 ${Number(basis.misses) || 0}(近 ${Number(basis.window) || sample} 次)`),
      pendingCount > 0 && e("span", { className: "text-slate-400" }, `另有 ${pendingCount} 条待对答案`),
      basis.note && e("span", { className: "text-slate-600", title: String(basis.note) }, "口径备注"),
    ),
    statusLine && e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-amber-300/90" }, statusLine),
  );
}

function actualCorrelation(runId: string): string {
  const suffix = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const safeRun = runId.trim().replace(/[^A-Za-z0-9._:-]/g, "-").slice(0, 80) || "run";
  return `prediction-actual-${safeRun}-${suffix}`;
}

function actualIdentityKey(
  runId: string,
  outcomeId: string,
  field: PredictionEvidenceField,
  metricPath: string,
  notes: string,
): string {
  const numericOutcome = Number(outcomeId);
  return JSON.stringify([
    runId.trim(),
    Number.isInteger(numericOutcome) ? numericOutcome : outcomeId.trim(),
    field,
    metricPath.trim(),
    notes.trim(),
  ]);
}

function PredictionActualReviewForm({ apiToken, onRecorded }: {
  apiToken: string;
  onRecorded: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [runId, setRunId] = React.useState("");
  const [outcomeId, setOutcomeId] = React.useState("");
  const [field, setField] = React.useState<PredictionEvidenceField>("window_28d");
  const [metricPath, setMetricPath] = React.useState("");
  const [notes, setNotes] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const identityKey = React.useMemo(
    () => actualIdentityKey(runId, outcomeId, field, metricPath, notes),
    [field, metricPath, notes, outcomeId, runId],
  );
  const [correlation, setCorrelation] = React.useState(() => ({
    identityKey: actualIdentityKey("", "", "window_28d", "", ""),
    value: actualCorrelation(""),
  }));

  React.useEffect(() => {
    setCorrelation((current) => current.identityKey === identityKey
      ? current
      : { identityKey, value: actualCorrelation(runId) });
  }, [identityKey, runId]);

  const submit = React.useCallback(async () => {
    const oid = Number(outcomeId);
    const normalizedMetricPath = metricPath.trim();
    if (
      !runId.trim()
      || runId.trim().length > 200
      || !Number.isInteger(oid)
      || oid <= 0
      || !/^[A-Za-z0-9_.-]{1,200}$/.test(normalizedMetricPath)
    ) {
      setMessage("请填写预测 run、已终结 outcome 编号和服务端指标路径");
      return;
    }
    setBusy(true);
    setMessage("");
    const activeCorrelation = correlation.identityKey === identityKey
      ? correlation.value
      : actualCorrelation(runId);
    if (correlation.identityKey !== identityKey) {
      setCorrelation({ identityKey, value: activeCorrelation });
    }
    try {
      const receipt = await recordPredictionActual(apiToken, runId.trim(), {
        outcome_id: oid,
        evidence_field: field,
        metric_path: normalizedMetricPath,
        correlation_id: activeCorrelation,
        notes: notes.trim() || undefined,
      });
      setMessage(`已记录真实结果 · eval #${receipt.id ?? "—"}${receipt.deduped ? "（幂等复用）" : ""}`);
      // 服务端已确认成功后才换新键；网络错误/响应丢失保留原键，重试可安全去重。
      setCorrelation({ identityKey, value: actualCorrelation(runId) });
      onRecorded();
    } catch (cause: any) {
      setMessage(String(cause?.message || "预测对答案失败"));
    } finally {
      setBusy(false);
    }
  }, [apiToken, correlation, field, identityKey, metricPath, notes, onRecorded, outcomeId, runId]);

  return (
    <div className="mt-2 rounded border border-white/[0.06] bg-black/15 p-2">
      <button type="button" onClick={() => setOpen((value) => !value)} className="text-[9px] text-cyan-300">
        {open ? "收起人工对答案" : "人工对答案（经理）"}
      </button>
      {open ? (
        <div className="mt-2 grid grid-cols-1 gap-1.5">
          <div className="grid grid-cols-2 gap-1.5">
            <input aria-label="预测 run ID" maxLength={200} value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="prediction run_id" className="h-7 rounded border border-white/10 bg-black/20 px-2 text-[9px] text-slate-100" />
            <input aria-label="Outcome ID" inputMode="numeric" value={outcomeId} onChange={(event) => setOutcomeId(event.target.value)} placeholder="finalized outcome_id" className="h-7 rounded border border-white/10 bg-black/20 px-2 text-[9px] text-slate-100" />
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <select aria-label="证据窗口" value={field} onChange={(event) => setField(event.target.value as PredictionEvidenceField)} className="h-7 rounded border border-white/10 bg-black/20 px-2 text-[9px] text-slate-100">
              <option value="actual_result">最终结果</option>
              <option value="window_7d">7 天观察窗</option>
              <option value="window_14d">14 天观察窗</option>
              <option value="window_28d">28 天观察窗</option>
            </select>
            <input aria-label="指标路径" maxLength={200} value={metricPath} onChange={(event) => setMetricPath(event.target.value)} placeholder="metrics.views_median" className="h-7 rounded border border-white/10 bg-black/20 px-2 text-[9px] text-slate-100" />
          </div>
          <input aria-label="对答案备注" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="可选：本次对答案说明" maxLength={1000} className="h-7 rounded border border-white/10 bg-black/20 px-2 text-[9px] text-slate-100" />
          <div className="flex items-center gap-2">
            <button type="button" disabled={busy} onClick={() => void submit()} className="rounded bg-cyan-500/80 px-2 py-1 text-[9px] text-white disabled:opacity-40">
              {busy ? "校验中…" : "从结果证据写入 actual"}
            </button>
            {message ? <span className="text-[9px] text-slate-400">{message}</span> : null}
          </div>
          <div className="text-[8.5px] text-slate-600">客户端不能填写 actual 数字；后端会核对产品、市场、渠道、周期和结果证据。</div>
        </div>
      ) : null}
    </div>
  );
}

export function PredictionLedgerPanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<LedgerSummaryResp | null>(null);
  const [reloadTick, setReloadTick] = React.useState(0);
  const activeToken = React.useRef("");

  // 只读拉取摘要(后端纯聚合已有数据,读得起);失败静默不渲染(非阻塞增益块)。
  React.useEffect(() => {
    const tokenChanged = activeToken.current !== apiToken;
    activeToken.current = apiToken;
    if (!apiToken) {
      setData(null);
      return;
    }
    if (tokenChanged) setData(null);
    let cancelled = false;
    void apiFetch<LedgerSummaryResp>("/api/admin/vkpi/prediction-ledger/summary", {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled && tokenChanged) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, reloadTick]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") !== "ok") return null; // 聚合失败:安静缺席,不甩后端报错

  const groups = Array.isArray(data.groups) ? data.groups : [];
  const totals = data.totals || {};
  const withSample = Number(totals.groups_with_sample) || 0;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "prediction-ledger",
      header: e(React.Fragment, null,
        e(Crosshair, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "预测台账 · 系统的预测有多准"),
        e("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200" },
          `${withSample}/${groups.length} 组有样本`),
      ),
    },
      groups.length === 0
        ? e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, "暂无任何台账分组数据")
        : e("div", { className: "space-y-1.5" }, groups.map((g, i) => GroupRow(g, i))),
      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:预测→结果对齐真实表(推荐 outcome+反馈 / 押注复盘 / 告警闭环 / 执行台账);样本<5 = 样本不足,驾照不得据此升级;台账永不影响任何评分。"),
      e(PredictionActualReviewForm, {
        apiToken,
        onRecorded: () => setReloadTick((value) => value + 1),
      }),
      data.generated_at && e("div", { className: "mt-0.5 text-[8.5px] text-slate-700 tabular-nums" },
        "生成于 " + String(data.generated_at).slice(0, 19).replace("T", " ") + " UTC"),
    ),
  );
}
