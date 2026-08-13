// S3 商业策略评估模拟器面板:「这笔预算怎么花最划算」what-if 决定性模拟。
// 数据:GET /api/admin/vkpi/strategy/simulate?sku=&budget_usd=&max_roster= —— 三策略并排
// (A 头部集中 / B 长尾铺量 / C 混合 50/50),全复用第4轮三引擎纯规则聚合,零 LLM 零采集。
// SKU 选择器复用 /api/admin/vkpi/sku/list(与 SKU 360°/发射台同源);预算输入 + 三方案对比卡
// + 推荐徽章(p50 成本效率排序;全员 CPM 兜底价方案 estimate_only 降级并警示)+ 每方案可展开名单。
// 诚实态:方案 status==="empty" 如实展示 reason;接口失败展示后端 reason,绝不本地编造数字。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { AlertTriangle, ChevronDown, Target, Trophy } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { kolHumanDisplayName, kolHumanPublicHandle } from "../lib/kolIdentity";

const e = React.createElement;

type Row = Record<string, any>;

type SkuItem = {
  sku: string; model_name?: string; marketing_name?: string;
  category_main?: string; mount?: string; price_usd?: number | null;
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(Math.round(v));
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

const RISK_TONE: Record<string, string> = {
  low: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  medium: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  high: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
};

function RiskChip(level: string | null | undefined) {
  const key = String(level || "");
  const tone = RISK_TONE[key] || "border-white/[0.1] text-slate-400";
  return e("span", { className: "rounded border px-1.5 py-0.5 text-[9px] " + tone },
    key ? `风险 ${key}` : "风险 —");
}

// 统计行:label + 主值 + 备注(全部来自后端 payload,不本地编造)。
function StatLine(label: string, value: string, sub?: string) {
  return e("div", { className: "flex items-baseline justify-between gap-2 text-[10px]" },
    e("span", { className: "shrink-0 text-slate-500" }, label),
    e("span", { className: "text-right" },
      e("span", { className: "tabular-nums font-medium text-slate-200" }, value),
      sub ? e("span", { className: "ml-1 text-[9px] tabular-nums text-slate-500" }, sub) : null,
    ),
  );
}

// 每方案可展开名单的成员行。
function MemberRow(m: Row, i: number) {
  const displayName = kolHumanDisplayName(m);
  const publicHandle = kolHumanPublicHandle(m);
  return e("div", { key: m.kol_pool_id ?? i, className: "flex items-center gap-1.5 rounded border border-white/[0.04] bg-black/20 px-1.5 py-1 text-[9.5px]" },
    e("span", { className: "shrink-0 text-[9px] tabular-nums text-slate-500" }, "#" + (i + 1)),
    e("span", { className: "min-w-0 flex-1 truncate text-slate-200", title: publicHandle ? `${displayName} ${publicHandle}` : displayName },
      displayName),
    m.tier && e("span", { className: "shrink-0 rounded bg-white/[0.05] px-1 py-0.5 text-[8.5px] text-slate-400" }, String(m.tier)),
    e("span", { className: "shrink-0 tabular-nums text-amber-200/90", title: "报价 p50(区间见后端 basis)" }, fmtUsd(m.cost_usd_p50)),
    e("span", { className: "shrink-0 tabular-nums text-slate-400", title: "预计播放 p50" },
      m.expected_views_p50 != null ? "播" + fmtNum(m.expected_views_p50) : "播?"),
    e("span", { className: "shrink-0 text-[8.5px] text-slate-600", title: m.forecast_note || "" },
      m.forecast_confidence || "无预测"),
  );
}

function PlanCard(plan: Row, rec: Row, expanded: boolean, onToggle: () => void) {
  const isRec = rec && rec.recommended_key === plan.key;
  const cost: Row = plan.cost_usd || {};
  const views: Row = plan.expected_views || {};
  const reach: Row = plan.dedup_reach || {};
  const members: Row[] = Array.isArray(plan.members) ? plan.members : [];
  const rankRow = ((rec && rec.ranking) || []).find((r: Row) => r.key === plan.key) || {};
  return e("div", {
    key: plan.key,
    className: "flex flex-col rounded-xl border p-2.5 " +
      (isRec ? "border-emerald-300/30 bg-emerald-500/[0.04]" : "border-white/[0.07] bg-white/[0.015]"),
  },
    // 头:方案名 + 推荐徽章 / 排名
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "text-[11px] font-semibold text-white" }, String(plan.name || plan.key)),
      isRec && e("span", { className: "flex items-center gap-1 rounded-full border border-emerald-300/40 bg-emerald-500/[0.15] px-1.5 py-0.5 text-[9px] text-emerald-100" },
        e(Trophy, { size: 9 }), "推荐"),
      !isRec && rankRow.rank != null && e("span", { className: "rounded border border-white/[0.08] px-1.5 py-0.5 text-[9px] text-slate-500" }, `第 ${rankRow.rank} 名`),
    ),
    e("div", { className: "mt-0.5 text-[9px] text-slate-500" }, String(plan.strategy || "")),

    plan.status !== "ready"
      ? e("div", { className: "mt-2 text-[10px] leading-relaxed text-amber-300/90" }, String(plan.reason || "该策略下无人可选(诚实空态)。"))
      : e(React.Fragment, null,
          e("div", { className: "mt-2 space-y-1" },
            StatLine("预计总播放 p50", fmtNum(views.p50_total), `(${fmtNum(views.p10_total)}–${fmtNum(views.p90_total)})`),
            StatLine("预计去重触达",
              reach.total_dedup_reach != null ? fmtNum(reach.total_dedup_reach) : "—",
              reach.total_dedup_reach == null ? String(reach.reason || reach.status || "") : (reach.dedup_saved_pct != null ? `去重省 ${reach.dedup_saved_pct}%` : "")),
            StatLine("总成本 p50", fmtUsd(cost.p50_total), `(${fmtUsd(cost.low_total)}–${fmtUsd(cost.high_total)})`),
            StatLine("成本每千播放", plan.cost_per_1k_views_p50 != null ? fmtUsd(plan.cost_per_1k_views_p50) : "—", "p50 口径"),
            StatLine("预算利用率", cost.utilization_pct != null ? cost.utilization_pct + "%" : "—", `剩 ${fmtUsd(cost.leftover_p50)}`),
          ),
          e("div", { className: "mt-1.5 flex flex-wrap items-center gap-1" },
            RiskChip((plan.risk || {}).level),
            e("span", { className: "rounded border border-white/[0.08] px-1.5 py-0.5 text-[9px] text-slate-400" }, `${plan.member_count} 人`),
            Number(views.views_unknown_members) > 0 && e("span", { className: "rounded border border-amber-300/25 px-1.5 py-0.5 text-[9px] text-amber-200/90", title: "无预测样本成员播放计 0,不硬补均值" },
              `${views.views_unknown_members} 人无预测`),
          ),
          // estimate_only 警示(全员 CPM 兜底价,对齐第4轮护栏)
          plan.estimate_only && e("div", { className: "mt-1.5 flex items-start gap-1 rounded border border-amber-300/25 bg-amber-500/[0.06] px-1.5 py-1 text-[9px] leading-relaxed text-amber-200/90" },
            e(AlertTriangle, { size: 9, className: "mt-0.5 shrink-0" }),
            e("span", null, String(plan.estimate_only_warning || "全员报价为 CPM 基准兜底,estimate_only。")),
          ),
          // 可展开名单
          e("button", {
            onClick: onToggle,
            className: "mt-2 flex w-full items-center justify-center gap-1 rounded border border-white/[0.06] bg-white/[0.02] px-2 py-1 text-[9.5px] text-slate-400 hover:bg-white/[0.05] hover:text-slate-200",
          },
            e(ChevronDown, { size: 10, className: "transition-transform " + (expanded ? "rotate-180" : "") }),
            expanded ? "收起名单" : `展开名单(${members.length} 人)`,
          ),
          expanded && e("div", { className: "mt-1.5 space-y-1" }, members.map((m, i) => MemberRow(m, i))),
        ),
  );
}

export function StrategySimPanel({ apiToken }: any) {
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState("");
  const [budgetText, setBudgetText] = React.useState("3000");
  const [data, setData] = React.useState<Row | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});

  // SKU 选择器搜索(300ms 防抖;复用 /sku/list 端点,与 SKU 360°/发射台同源)。
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      void apiFetch<{ items?: SkuItem[] }>(
        `/api/admin/vkpi/sku/list?query=${encodeURIComponent(query.trim())}&limit=30`,
        { timeoutMs: 6000 },
        apiToken,
      )
        .then((res) => { if (alive) setOptions(Array.isArray(res?.items) ? res.items : []); })
        .catch(() => { if (alive) setOptions([]); })
        .finally(() => { if (alive) setSearching(false); });
    }, 300);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [apiToken, query]);

  const runSim = React.useCallback((sku: string, budgetStr: string) => {
    const budget = Number(budgetStr);
    if (!apiToken || !sku) return;
    if (!Number.isFinite(budget) || budget <= 0) {
      setError("预算需为正数(USD)。");
      return;
    }
    setSelectedSku(sku);
    setDropdownOpen(false);
    setLoading(true);
    setError("");
    setData(null);
    setExpanded({});
    void apiFetch<Row>(
      `/api/admin/vkpi/strategy/simulate?sku=${encodeURIComponent(sku)}&budget_usd=${encodeURIComponent(String(budget))}&max_roster=20`,
      { timeoutMs: 120000 },
      apiToken,
    )
      .then((res) => setData(res && typeof res === "object" ? res : null))
      .catch((err: any) => setError(String(err?.detail || err?.message || "模拟失败")))
      .finally(() => setLoading(false));
  }, [apiToken]);

  if (!apiToken) return null;

  const plans: Row[] = Array.isArray(data?.plans) ? (data!.plans as Row[]) : [];
  const rec: Row = (data?.recommendation as Row) || {};
  const cp: Row = (data?.candidate_pool as Row) || {};

  return e("div", { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-3" },
    // 标题
    e("div", { className: "flex items-center gap-1.5" },
      e(Target, { size: 12, className: "text-emerald-300" }),
      e("span", { className: "text-[11px] font-semibold text-white" }, "商业策略模拟器 · 这笔预算怎么花最划算"),
    ),
    e("div", { className: "mt-0.5 text-[9.5px] text-slate-500" },
      "选 SKU + 填预算 → 三策略 what-if 并排(头部集中 / 长尾铺量 / 混合 50/50);零 LLM 决定性模拟,数字全带 basis 可追溯。"),

    // 控件:SKU 选择器 + 预算输入
    e("div", { className: "mt-2 flex gap-1.5" },
      e("div", { className: "relative flex-1" },
        e("input", {
          value: query,
          onChange: (ev: any) => { setQuery(ev.target.value); setDropdownOpen(true); },
          onFocus: () => setDropdownOpen(true),
          placeholder: "搜索 SKU / 型号,如 AF-85MM-F14-PRO-FE…",
          className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1.5 text-[10.5px] text-white placeholder:text-slate-600 outline-none focus:border-emerald-300/40",
        }),
        dropdownOpen && e("div", { className: "absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-white/[0.1] bg-[#10151f] shadow-xl" },
          searching && e("div", { className: "px-2 py-1.5 text-[10px] text-slate-500" }, "搜索中…"),
          !searching && options.length === 0 && e("div", { className: "px-2 py-1.5 text-[10px] text-slate-500" }, "无匹配 SKU"),
          !searching && options.map((opt) => e("button", {
            key: opt.sku,
            onClick: () => runSim(opt.sku, budgetText),
            className: "flex w-full items-center justify-between gap-2 border-b border-white/[0.04] px-2 py-1.5 text-left last:border-0 hover:bg-white/[0.05]",
          },
            e("span", { className: "min-w-0" },
              e("span", { className: "block truncate text-[10px] text-slate-200" }, opt.model_name || opt.marketing_name || opt.sku),
              e("span", { className: "block truncate text-[9px] text-slate-500" }, opt.sku + (opt.mount ? ` · ${opt.mount}` : "")),
            ),
            e("span", { className: "shrink-0 text-[9px] text-slate-500" }, opt.price_usd != null ? `$${opt.price_usd}` : ""),
          )),
        ),
      ),
      e("input", {
        value: budgetText,
        onChange: (ev: any) => setBudgetText(ev.target.value.replace(/[^0-9.]/g, "")),
        inputMode: "decimal",
        title: "预算(USD)",
        className: "w-[88px] rounded-lg border border-white/[0.08] bg-white/[0.03] px-2 py-1.5 text-right text-[10.5px] tabular-nums text-amber-200 outline-none focus:border-emerald-300/40",
      }),
      e("button", {
        onClick: () => { if (selectedSku) runSim(selectedSku, budgetText); },
        disabled: !selectedSku || loading,
        className: "shrink-0 rounded-lg border border-emerald-300/30 bg-emerald-500/[0.12] px-2.5 py-1.5 text-[10px] text-emerald-100 hover:bg-emerald-500/[0.2] disabled:cursor-not-allowed disabled:opacity-40",
        title: selectedSku ? `重跑 ${selectedSku}` : "先在左侧选一个 SKU",
      }, loading ? "模拟中…" : "模拟"),
    ),

    // 状态区
    error && e("div", { className: "mt-2 rounded border border-rose-300/25 bg-rose-500/[0.06] px-2 py-1.5 text-[10px] text-rose-200" }, error),
    loading && e("div", { className: "mt-3 py-4 text-center text-[10px] text-slate-500" }, "三策略并排模拟中(逐人报价 + 预测 + 去重触达)…"),
    !loading && !data && !error && e("div", { className: "mt-2 rounded border border-dashed border-white/[0.08] px-2 py-3 text-center text-[10px] text-slate-600" },
      "选一个 SKU 并填预算,即刻对比三种花法。"),

    // 结果区
    !loading && data && data.status !== "ready" && e("div", { className: "mt-2 rounded border border-amber-300/25 bg-amber-500/[0.05] px-2 py-1.5 text-[10px] leading-relaxed text-amber-200/90" },
      String(data.reason || "模拟无结果(诚实空态)。")),
    !loading && data && data.status === "ready" && e(React.Fragment, null,
      // 推荐条
      rec.status === "ready"
        ? e("div", { className: "mt-2 flex items-start gap-1.5 rounded-lg border border-emerald-300/25 bg-emerald-500/[0.06] px-2 py-1.5" },
            e(Trophy, { size: 11, className: "mt-0.5 shrink-0 text-emerald-300" }),
            e("div", { className: "min-w-0 text-[10px] leading-relaxed" },
              e("span", { className: "font-medium text-emerald-100" }, `推荐:${rec.recommended_name || rec.recommended_key}`),
              e("span", { className: "ml-1 text-slate-400" }, `(置信 ${rec.confidence || "—"})`),
              e("div", { className: "text-[9px] text-slate-500" }, String(rec.rule || "")),
              rec.warning && e("div", { className: "mt-0.5 text-[9px] text-amber-200/90" }, "⚠ " + String(rec.warning)),
            ),
          )
        : e("div", { className: "mt-2 rounded border border-white/[0.08] px-2 py-1.5 text-[10px] text-slate-500" },
            String(rec.reason || "无法给出推荐(诚实空态)。")),
      // 三方案对比卡
      e("div", { className: "mt-2 grid grid-cols-1 gap-2 md:grid-cols-3" },
        plans.map((p) => PlanCard(p, rec, !!expanded[String(p.key)],
          () => setExpanded((prev) => ({ ...prev, [String(p.key)]: !prev[String(p.key)] })))),
      ),
      // 口径脚注
      e("div", { className: "mt-2 text-[9px] leading-relaxed text-slate-600" },
        `候选池 ${cp.size ?? "—"} 人(可估价 ${cp.priceable_count ?? "—"} / 可预测 ${cp.forecastable_count ?? "—"} / nano-micro ${cp.longtail_count ?? "—"});`
        + "口径:成本=rate_card,播放区间=预测战绩逐人求和(p10/p50/p90),触达=组合去重;同输入同输出,缺数据诚实空态,不参与 V6 Fit 评分。"),
    ),
  );
}
