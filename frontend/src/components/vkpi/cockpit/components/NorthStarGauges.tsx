// U3 会呼吸的指挥室 · NorthStarGauges:90 天北极星三表盘。
// ----------------------------------------------------------------------------
// launch brief n/30 · Dealer n/300 · 裁决率 n%/30% —— 三个大进度环挂 GTM Command 顶部。
// 自拉取 GET /api/admin/vkpi/gtm/northstar(纯读,真库现查),自判空:
//   端点缺/错 → 一行诚实降级提示;表缺 → 环显 0 + 「表缺」徽标(诚实展示欠账正是目的)。
// 审美纪律:进度环入场扫描补间一次(300ms,依次错峰),无循环闪烁;
//   prefers-reduced-motion 直显终值(useReducedMotion);数字直显不跳动(信息优先)。
// 深色 slate/白 6% 透明度体系;sky/emerald/purple 均为页面既有语义色,不引新色板。

import React, { useEffect, useState } from "react";
import { m, useReducedMotion } from "framer-motion";
import { Target } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type Row = Record<string, any>;

interface GaugeMetric {
  key: string;
  label: string;
  value: number;
  target: number;
  unit: string;
  status: string;
  note: string;
  detail: string;
}

function asNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return 0;
}

function asStr(v: unknown): string {
  return typeof v === "string" ? v : "";
}

const METRIC_DEFS: Array<{ key: string; label: string; target: number; unit: string; color: string; dim: string }> = [
  { key: "launch_briefs", label: "Launch Brief", target: 30, unit: "份", color: "rgba(125,211,252,0.9)", dim: "text-sky-200" },
  { key: "dealers", label: "Dealer", target: 300, unit: "行", color: "rgba(52,211,153,0.9)", dim: "text-emerald-200" },
  { key: "verdict_rate", label: "裁决率", target: 30, unit: "%", color: "rgba(216,180,254,0.9)", dim: "text-purple-200" },
];

export function normalizeNorthstar(raw: unknown): { metrics: GaugeMetric[]; generatedAt: string } | null {
  const data = raw && typeof raw === "object" ? (raw as Row) : {};
  if (asStr(data.status) === "error") return null;
  const src = data.metrics && typeof data.metrics === "object" ? (data.metrics as Row) : null;
  if (!src) return null;
  const metrics: GaugeMetric[] = METRIC_DEFS.map((def) => {
    const m: Row = src[def.key] && typeof src[def.key] === "object" ? src[def.key] : {};
    const decided = m.decided != null ? asNum(m.decided) : null;
    const total = m.total != null ? asNum(m.total) : null;
    return {
      key: def.key,
      label: asStr(m.label) || def.label,
      value: asNum(m.value),
      target: asNum(m.target) || def.target,
      unit: asStr(m.unit) || def.unit,
      status: asStr(m.status),
      note: asStr(m.note),
      detail: def.key === "verdict_rate" && decided !== null && total !== null ? `${decided}/${total} 已裁决` : "",
    };
  });
  return { metrics, generatedAt: asStr(data.generated_at) };
}

// 单表盘:SVG 进度环(入场扫描一次)+ 中心大数字(直显,不动画 —— 信息优先)。
function Gauge({ metric, color, dim, reduced, delay }: { metric: GaugeMetric; color: string; dim: string; reduced: boolean; delay: number }) {
  const R = 26;
  const C = 2 * Math.PI * R;
  const pct = metric.target > 0 ? Math.max(0, Math.min(1, metric.value / metric.target)) : 0;
  const dashTarget = C * (1 - pct);
  const valueText = metric.unit === "%"
    ? `${Number.isInteger(metric.value) ? metric.value : metric.value.toFixed(1)}%`
    : String(Math.round(metric.value));
  const targetText = metric.unit === "%" ? `${metric.target}%` : String(metric.target);
  const missing = metric.status && metric.status !== "ok";
  const ringProps = {
    cx: 32, cy: 32, r: R, fill: "none", stroke: color, strokeWidth: 5,
    strokeLinecap: "round" as const, strokeDasharray: C, transform: "rotate(-90 32 32)",
  };
  return e(
    "div",
    { className: "flex min-w-0 flex-1 items-center gap-3", "data-testid": `northstar-gauge-${metric.key}` },
    e(
      "svg",
      { viewBox: "0 0 64 64", className: "h-16 w-16 shrink-0", role: "img", "aria-label": `${metric.label} ${valueText} / ${targetText}` },
      e("circle", { cx: 32, cy: 32, r: R, fill: "none", stroke: "rgba(255,255,255,0.06)", strokeWidth: 5 }),
      reduced
        ? e("circle", { ...ringProps, strokeDashoffset: dashTarget })
        : e(m.circle, {
            ...ringProps,
            initial: { strokeDashoffset: C },
            animate: { strokeDashoffset: dashTarget },
            transition: { duration: 0.3, delay, ease: [0.16, 1, 0.3, 1] },
          }),
      e(
        "text",
        { x: 32, y: 36, textAnchor: "middle", className: "fill-white", style: { fontSize: valueText.length > 4 ? 11 : 14, fontWeight: 600 } },
        valueText,
      ),
    ),
    e(
      "div",
      { className: "min-w-0" },
      e(
        "div",
        { className: "flex items-center gap-1.5" },
        e("span", { className: `text-[11px] font-semibold ${dim}` }, metric.label),
        missing
          ? e(
              "span",
              {
                className: "rounded border border-amber-300/25 bg-amber-500/[0.08] px-1 py-0.5 text-[8.5px] text-amber-200",
                title: metric.note || metric.status,
              },
              metric.status === "table_missing" ? "表缺" : "指标异常",
            )
          : null,
      ),
      e("div", { className: "text-[10px] tabular-nums text-slate-400" }, `${valueText} / ${targetText}${metric.unit !== "%" ? ` ${metric.unit}` : ""}`),
      metric.detail ? e("div", { className: "text-[9px] text-slate-600" }, metric.detail) : null,
    ),
  );
}

export function NorthStarGauges({ apiToken = "" }: { apiToken?: string }) {
  const reduced = !!useReducedMotion();
  const [data, setData] = useState<{ metrics: GaugeMetric[]; generatedAt: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<Row>("/api/admin/vkpi/gtm/northstar", { timeoutMs: 10000 }, apiToken)
      .then((res) => {
        if (!alive) return;
        const parsed = normalizeNorthstar(res);
        if (parsed) setData(parsed);
        else setFailed(true);
      })
      .catch(() => { if (alive) setFailed(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [apiToken]);

  if (!apiToken) return null;

  return e(
    "div",
    { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4", "data-testid": "northstar-gauges" },
    e(
      "div",
      { className: "mb-3 flex items-start justify-between gap-2" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Target, { size: 14, className: "text-sky-300" }),
        e("div", { className: "text-[13px] font-semibold text-white" }, "90 天北极星"),
      ),
      e("div", { className: "text-[10px] text-slate-500" }, "真库现查 · 表缺诚实 0"),
    ),
    loading && !data
      ? e("div", { className: "py-3 text-center text-[11px] text-slate-500" }, "北极星指标加载中…")
      : failed || !data
        ? e(
            "div",
            { className: "rounded-lg border border-dashed border-white/[0.08] px-3 py-2 text-center text-[11px] text-slate-500" },
            "北极星端点暂不可用(诚实降级,不编数)。",
          )
        : e(
            "div",
            { className: "flex flex-col gap-4 sm:flex-row sm:items-center" },
            data.metrics.map((m, i) => {
              const def = METRIC_DEFS[i];
              return e(Gauge, { key: m.key, metric: m, color: def.color, dim: def.dim, reduced, delay: i * 0.08 });
            }),
          ),
    data?.generatedAt
      ? e("div", { className: "mt-2 text-right text-[9px] text-slate-600" }, `生成于 ${data.generatedAt}(UTC)· 纯读`)
      : null,
  );
}

export default NorthStarGauges;
