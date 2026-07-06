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

export function PredictionLedgerPanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<LedgerSummaryResp | null>(null);

  // 只读拉取摘要(后端纯聚合已有数据,读得起);失败静默不渲染(非阻塞增益块)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<LedgerSummaryResp>("/api/admin/vkpi/prediction-ledger/summary", {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

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
      data.generated_at && e("div", { className: "mt-0.5 text-[8.5px] text-slate-700 tabular-nums" },
        "生成于 " + String(data.generated_at).slice(0, 19).replace("T", " ") + " UTC"),
    ),
  );
}
