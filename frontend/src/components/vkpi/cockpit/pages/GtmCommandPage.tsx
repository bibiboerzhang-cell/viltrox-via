import React from "react";
import { Compass } from "lucide-react";
import { StrategySimPanel } from "../components/StrategySimPanel";
import { NorthStarGauges } from "../components/NorthStarGauges";
import { ThresholdBar } from "../components/ThresholdBar";
import {
  getGtmPlanPreview,
  getMarketBrainSummary,
  listSkuOptions,
} from "../../../../services/vkpi/gtmCommand-api";
import type {
  GtmActionItem,
  GtmGoal,
  GtmPlanPreview,
  GtmPlanSection,
  LearningDigest,
  MarketBrainSummary,
  SkuListItem,
} from "../../../../services/vkpi/gtmCommand-api";

// GTM-1 · W3 · GTM Command|上市增长指挥图(不叫分析面板)。
//   五区块:①主判断 ②条件化预判 ③增长路线图 ④今日行动 ⑤复盘学习。
//   无 SKU → 吃 /market-brain/summary 显全局五卡;选 SKU+国家/预算/目标 → 打 gtm-plan/preview。
//   【显示层宪法自查】本页只吃 public_plan,渲染全部走点名白名单键(见 pickTitle/pickSub
//   与各卡的显式字段);score_details / raw_* / competitor_notes / 黑名单等 private 字段
//   即使后端多给,也已在 gtmCommand-api 的 stripPrivateFields 深度剥除,页面代码零引用。
//   KOL 风险只出标签(risk_labels);预判强制条件化四段式(预判/依据+置信/触发加码/撤退条件)。
//   人审执行按钮 v1 占位 disabled(title「GTM-3 接线」)。失败安静:每卡自判空,单卡错不拖垮整页。

const e = React.createElement;

const GOAL_OPTIONS: Array<{ value: GtmGoal; label: string }> = [
  { value: "exposure", label: "曝光" },
  { value: "conversion", label: "转化" },
  { value: "content", label: "素材" },
  { value: "channel", label: "渠道铺货" },
];

const CONF_TONE: Record<string, string> = {
  high: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  medium: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  low: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
};

function confBadge(confidence: string, prefix = "置信") {
  const key = confidence.toLowerCase();
  const tone = CONF_TONE[key] || "border-white/[0.1] bg-white/[0.04] text-slate-300";
  return e(
    "span",
    { className: `shrink-0 rounded-md border px-2 py-0.5 text-[10px] ${tone}` },
    confidence ? `${prefix} ${confidence}` : `${prefix} —`,
  );
}

function Card({ title, hint, extra, children }: { title: string; hint?: string; extra?: React.ReactNode; children?: React.ReactNode }) {
  return e(
    "div",
    { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4" },
    e(
      "div",
      { className: "mb-3 flex items-start justify-between gap-2" },
      e(
        "div",
        { className: "min-w-0" },
        e("div", { className: "text-[13px] font-semibold text-white" }, title),
        hint ? e("div", { className: "mt-0.5 text-[10px] text-slate-500" }, hint) : null,
      ),
      extra ?? null,
    ),
    children,
  );
}

function Empty({ text }: { text: string }) {
  return e(
    "div",
    { className: "rounded-lg border border-dashed border-white/[0.08] px-3 py-3 text-center text-[11px] text-slate-500" },
    text,
  );
}

// U3 · preview 加载骨架屏(U2 骨架基元未就绪 → 本页极简版,收口时统一替换)。
// 五卡轮廓 + animate-pulse;motion-reduce:animate-none 尊重 prefers-reduced-motion;
// 加载态短暂脉动非常驻循环,结束即卸载。诚实脚注保留原文案。
function PreviewSkeleton() {
  return e(
    "div",
    { className: "space-y-4", role: "status", "aria-label": "作战预览聚合中", "data-testid": "preview-skeleton" },
    [0, 1, 2, 3, 4].map((i) =>
      e(
        "div",
        { key: i, className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4 animate-pulse motion-reduce:animate-none" },
        e("div", { className: "h-3 w-40 rounded bg-white/[0.06]" }),
        e("div", { className: "mt-3 h-2.5 w-full rounded bg-white/[0.04]" }),
        e("div", { className: "mt-2 h-2.5 w-2/3 rounded bg-white/[0.04]" }),
      ),
    ),
    e("div", { className: "text-center text-[11px] text-slate-500" }, "作战预览聚合中(纯读,零写库)…"),
  );
}

// 渠道段通用渲染:只点名白名单键(显示层宪法),形状未知也不 JSON 倾倒。
function pickTitle(row: Record<string, any>): string {
  for (const k of ["display_name", "handle", "name", "channel", "angle", "title", "action", "template", "metric", "plan_name", "sku"]) {
    if (typeof row[k] === "string" && row[k]) return row[k];
  }
  return "—";
}

function pickSub(row: Record<string, any>): string {
  const parts: string[] = [];
  for (const k of ["play", "reason", "why_fit", "note", "basis", "summary", "format", "split", "suggestion", "recent_highlight"]) {
    if (typeof row[k] === "string" && row[k]) {
      parts.push(row[k]);
      if (parts.length >= 2) break;
    }
  }
  return parts.join(" · ");
}

function riskLabels(row: Record<string, any>): string[] {
  const v = row.risk_labels ?? row.risk_tags;
  if (!Array.isArray(v)) return [];
  return v.filter((x) => typeof x === "string" && x).slice(0, 4);
}

function SectionList({ section, emptyText, max = 6 }: { section: GtmPlanSection; emptyText: string; max?: number }) {
  if (section.status && section.status !== "ok" && section.status !== "ready") {
    return e(
      "div",
      { className: "rounded-lg border border-amber-300/20 bg-amber-500/[0.05] px-3 py-2 text-[11px] leading-relaxed text-amber-200/90" },
      section.note || `该段暂不可用(${section.status},诚实空态)。`,
    );
  }
  if (section.items.length === 0) {
    return e(
      "div",
      null,
      e(Empty, { text: section.note || emptyText }),
    );
  }
  return e(
    "div",
    { className: "space-y-1.5" },
    section.items.slice(0, max).map((row, i) =>
      e(
        "div",
        { key: i, className: "rounded-lg border border-white/[0.06] px-2.5 py-1.5" },
        e(
          "div",
          { className: "flex flex-wrap items-center gap-1.5" },
          e("span", { className: "text-[11.5px] text-slate-200" }, pickTitle(row)),
          typeof row.confidence === "string" && row.confidence ? confBadge(row.confidence) : null,
          riskLabels(row).map((r, j) =>
            e("span", { key: j, className: "rounded border border-rose-300/25 bg-rose-500/[0.08] px-1.5 py-0.5 text-[9.5px] text-rose-200" }, r),
          ),
        ),
        pickSub(row) ? e("div", { className: "mt-0.5 text-[10px] leading-relaxed text-slate-500" }, pickSub(row)) : null,
      ),
    ),
    section.note ? e("div", { className: "mt-1 text-[10px] text-slate-500" }, section.note) : null,
  );
}

// 六要素行动行:原因/证据摘要/成本/风险/预计收益/人审按钮(v1 disabled,GTM-3 接线)。
function ActionRow({ item }: { item: GtmActionItem }) {
  const cells: Array<[string, string]> = [
    ["原因", item.reason],
    ["证据摘要", item.evidence_summary],
    ["成本", item.cost_note],
    ["风险", item.risk],
    ["预计收益", item.expected_gain],
  ];
  return e(
    "div",
    { className: "rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
    e(
      "div",
      { className: "flex items-start justify-between gap-2" },
      e("div", { className: "min-w-0 text-[12px] font-medium text-slate-100" }, item.action || "—"),
      e(
        "button",
        {
          disabled: true,
          title: "GTM-3 接线",
          className: "shrink-0 cursor-not-allowed rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[10px] text-slate-500 opacity-60",
        },
        "人审执行",
      ),
    ),
    e(
      "div",
      { className: "mt-1.5 grid grid-cols-1 gap-x-3 gap-y-1 md:grid-cols-2" },
      cells.map(([label, value], i) =>
        e(
          "div",
          { key: i, className: "flex gap-1.5 text-[10.5px] leading-relaxed" },
          e("span", { className: "shrink-0 text-slate-500" }, `${label}:`),
          e("span", { className: "min-w-0 text-slate-300" }, value || "—"),
        ),
      ),
    ),
    item.ref ? e("div", { className: "mt-1 text-[9.5px] text-slate-600" }, `ref: ${item.ref}`) : null,
  );
}

function ActionsBlock({ items, note, emptyText }: { items: GtmActionItem[]; note?: string; emptyText: string }) {
  return e(
    "div",
    null,
    items.length === 0
      ? e(Empty, { text: emptyText })
      : e("div", { className: "space-y-2" }, items.slice(0, 10).map((it, i) => e(ActionRow, { key: i, item: it }))),
    note ? e("div", { className: "mt-2 text-[10px] text-slate-500" }, note) : null,
  );
}

function LearningBlock({ digest, footnote }: { digest: LearningDigest; footnote: string }) {
  const groups: Array<[string, string[]]> = [
    ["本轮验证了什么", digest.validated],
    ["哪些内容风格有效", digest.effective_styles],
    ["哪些渠道不值", digest.dropped_channels],
  ];
  const allEmpty = groups.every(([, arr]) => arr.length === 0) && !digest.next_change;
  return e(
    "div",
    null,
    allEmpty
      ? e(Empty, { text: digest.honesty_note || "复盘账本暂无可总结条目(样本不足,诚实空态)。" })
      : e(
          "div",
          { className: "grid grid-cols-1 gap-3 md:grid-cols-3" },
          groups.map(([label, arr], i) =>
            e(
              "div",
              { key: i, className: "rounded-lg border border-white/[0.06] px-3 py-2" },
              e("div", { className: "text-[10px] text-slate-500" }, label),
              arr.length === 0
                ? e("div", { className: "mt-1 text-[11px] text-slate-600" }, "暂无")
                : e(
                    "ul",
                    { className: "mt-1 list-disc space-y-0.5 pl-4 text-[11px] leading-relaxed text-slate-300" },
                    arr.slice(0, 5).map((s, j) => e("li", { key: j }, s)),
                  ),
            ),
          ),
        ),
    digest.next_change
      ? e(
          "div",
          { className: "mt-2 rounded-lg border border-sky-300/20 bg-sky-500/[0.06] px-3 py-2 text-[11px] leading-relaxed text-sky-100" },
          `下次推荐怎么变:${digest.next_change}`,
        )
      : null,
    e("div", { className: "mt-2 text-[10px] leading-relaxed text-slate-600" }, [digest.honesty_note, footnote].filter(Boolean).join(" · ")),
  );
}

export function GtmCommandPage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  // 全局五卡
  const [summary, setSummary] = React.useState<MarketBrainSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = React.useState(false);
  const [summaryError, setSummaryError] = React.useState("");
  // SKU 选择器(复用 /sku/list,与 SKU 360°/模拟器同源)
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState("");
  // 预览输入
  const [country, setCountry] = React.useState("US");
  const [budgetText, setBudgetText] = React.useState("3000");
  const [goal, setGoal] = React.useState<GtmGoal>("conversion");
  const [windowDays, setWindowDays] = React.useState(30);
  // 预览结果
  const [plan, setPlan] = React.useState<GtmPlanPreview | null>(null);
  const [planLoading, setPlanLoading] = React.useState(false);
  const [planError, setPlanError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSummaryLoading(true);
    getMarketBrainSummary(apiToken)
      .then((res) => { if (alive) setSummary(res); })
      .catch((err: any) => { if (alive) setSummaryError(String(err?.detail || err?.message || "全局摘要加载失败")); })
      .finally(() => { if (alive) setSummaryLoading(false); });
    return () => { alive = false; };
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      listSkuOptions(apiToken, query)
        .then((items) => { if (alive) setOptions(items); })
        .catch(() => { if (alive) setOptions([]); })
        .finally(() => { if (alive) setSearching(false); });
    }, 300);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [apiToken, query]);

  const loadPreview = React.useCallback(
    (sku: string) => {
      if (!apiToken || !sku) return;
      setSelectedSku(sku);
      setDropdownOpen(false);
      setPlanLoading(true);
      setPlanError("");
      setPlan(null);
      const budget = Number(budgetText);
      getGtmPlanPreview(apiToken, {
        sku,
        country,
        budgetUsd: Number.isFinite(budget) && budget > 0 ? budget : 3000,
        goal,
        windowDays,
      })
        .then((res) => setPlan(res))
        .catch((err: any) => setPlanError(String(err?.detail || err?.message || "作战预览生成失败")))
        .finally(() => setPlanLoading(false));
    },
    [apiToken, budgetText, country, goal, windowDays],
  );

  const p = plan?.public_plan;
  const meta = plan?.meta;
  const digest: LearningDigest = summary?.learning_digest || {
    validated: [], effective_styles: [], dropped_channels: [], next_change: "", honesty_note: "", status: "",
  };

  // ---- 顶部与控件 ----
  const header = e(
    "div",
    null,
    e(
      "div",
      { className: "flex items-center gap-2" },
      e(Compass, { size: 18, className: "text-sky-300" }),
      e("div", { className: "text-[18px] font-semibold text-white" }, "GTM Command · 上市增长指挥图"),
    ),
    e(
      "div",
      { className: "mt-0.5 text-[12px] text-slate-400" },
      "GTM Command 把产品、市场、KOL、渠道和历史结果合成作战路线。",
    ),
  );

  const controls = e(
    "div",
    { className: "flex flex-wrap items-start gap-2" },
    // SKU 选择器
    e(
      "div",
      { className: "relative min-w-[220px] flex-1" },
      e("input", {
        value: query,
        onChange: (ev: any) => { setQuery(ev.target.value); setDropdownOpen(true); },
        onFocus: () => setDropdownOpen(true),
        placeholder: "搜索 SKU / 型号,如 AF-85MM-F14-PRO-FE…",
        className: "w-full rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-white placeholder:text-slate-600 outline-none focus:border-sky-300/40",
      }),
      dropdownOpen &&
        e(
          "div",
          { className: "absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-white/[0.1] bg-[#10151f] shadow-xl" },
          searching && e("div", { className: "px-3 py-2 text-[11px] text-slate-500" }, "搜索中…"),
          !searching && options.length === 0 && e("div", { className: "px-3 py-2 text-[11px] text-slate-500" }, "无匹配 SKU"),
          !searching &&
            options.map((opt) =>
              e(
                "button",
                {
                  key: opt.sku,
                  onClick: () => loadPreview(opt.sku),
                  className: "flex w-full items-center justify-between gap-2 border-b border-white/[0.04] px-3 py-1.5 text-left last:border-0 hover:bg-white/[0.05]",
                },
                e(
                  "span",
                  { className: "min-w-0" },
                  e("span", { className: "block truncate text-[11px] text-slate-200" }, opt.model_name || opt.marketing_name || opt.sku),
                  e("span", { className: "block truncate text-[9.5px] text-slate-500" }, opt.sku + (opt.mount ? ` · ${opt.mount}` : "")),
                ),
                e("span", { className: "shrink-0 text-[10px] text-slate-500" }, opt.price_usd != null ? `$${opt.price_usd}` : ""),
              ),
            ),
        ),
    ),
    // 国家 / 预算 / 目标 / 窗口
    e("input", {
      value: country,
      onChange: (ev: any) => setCountry(ev.target.value.toUpperCase().slice(0, 2)),
      title: "国家(ISO 两位码,v1 只影响受众地域口径)",
      placeholder: "US",
      className: "w-[52px] rounded-xl border border-white/[0.08] bg-white/[0.03] px-2 py-2 text-center text-[12px] text-white outline-none focus:border-sky-300/40",
    }),
    e("input", {
      value: budgetText,
      onChange: (ev: any) => setBudgetText(ev.target.value.replace(/[^0-9.]/g, "")),
      inputMode: "decimal",
      title: "预算(USD,默认 3000)",
      className: "w-[84px] rounded-xl border border-white/[0.08] bg-white/[0.03] px-2 py-2 text-right text-[12px] tabular-nums text-amber-200 outline-none focus:border-sky-300/40",
    }),
    e(
      "select",
      {
        value: goal,
        onChange: (ev: any) => setGoal(ev.target.value as GtmGoal),
        title: "目标主线",
        className: "rounded-xl border border-white/[0.08] bg-[#10151f] px-2 py-2 text-[12px] text-slate-200 outline-none focus:border-sky-300/40",
      },
      GOAL_OPTIONS.map((g) => e("option", { key: g.value, value: g.value }, g.label)),
    ),
    e(
      "select",
      {
        value: String(windowDays),
        onChange: (ev: any) => setWindowDays(Number(ev.target.value) || 30),
        title: "预判窗口(天)",
        className: "rounded-xl border border-white/[0.08] bg-[#10151f] px-2 py-2 text-[12px] text-slate-200 outline-none focus:border-sky-300/40",
      },
      [7, 14, 30].map((d) => e("option", { key: d, value: String(d) }, `${d} 天`)),
    ),
    e(
      "button",
      {
        onClick: () => { if (selectedSku) loadPreview(selectedSku); },
        disabled: !selectedSku || planLoading,
        title: selectedSku ? `重新生成 ${selectedSku} 的作战预览` : "先在左侧选一个 SKU",
        className: "rounded-xl border border-sky-300/30 bg-sky-500/[0.12] px-3 py-2 text-[12px] text-sky-100 hover:bg-sky-500/[0.2] disabled:cursor-not-allowed disabled:opacity-40",
      },
      planLoading ? "生成中…" : "生成作战预览",
    ),
    plan &&
      e(
        "button",
        {
          onClick: () => { setPlan(null); setPlanError(""); setSelectedSku(""); },
          className: "rounded-xl border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-[12px] text-slate-300 hover:bg-white/[0.06]",
        },
        "返回全局",
      ),
  );

  // ---- 全局五卡(无 SKU 模式) ----
  const globalCards = summary &&
    e(
      "div",
      { className: "space-y-4" },
      e(
        Card,
        { title: "① 本周信号", hint: "内部信号聚合(brand pulse / 赛道 / 市场之声)—— 外部雷达 GTM-5 待接" },
        summary.weekly_signals.items.length === 0
          ? e(Empty, { text: summary.weekly_signals.sources_note || "本周暂无可用信号(诚实空态)。" })
          : e(
              "div",
              { className: "space-y-1.5" },
              summary.weekly_signals.items.slice(0, 8).map((s, i) =>
                e(
                  "div",
                  { key: i, className: "flex flex-wrap items-center gap-1.5 rounded-lg border border-white/[0.06] px-2.5 py-1.5" },
                  e("span", { className: "rounded border border-white/[0.08] px-1.5 py-0.5 text-[9.5px] text-slate-400" }, s.kind || "signal"),
                  e("span", { className: "min-w-0 text-[11.5px] text-slate-200" }, s.signal || "—"),
                  e(
                    "span",
                    { className: "ml-auto flex shrink-0 items-center gap-1.5 text-[9.5px] text-slate-500" },
                    s.freshness ? e("span", null, s.freshness) : null,
                    s.sample_size != null ? e("span", null, `样本 ${s.sample_size}`) : null,
                  ),
                  confBadge(s.confidence),
                ),
              ),
            ),
        summary.weekly_signals.sources_note && summary.weekly_signals.items.length > 0
          ? e("div", { className: "mt-2 text-[10px] text-slate-600" }, summary.weekly_signals.sources_note)
          : null,
      ),
      e(
        Card,
        { title: "② 产品机会", hint: "赛道位次 × 内容表现 × 人群画像 —— 全库内既有数据" },
        summary.product_opportunities.items.length === 0
          ? e(Empty, { text: summary.product_opportunities.note || "暂无成型产品机会(诚实空态)。" })
          : e(
              "div",
              { className: "space-y-1.5" },
              summary.product_opportunities.items.slice(0, 6).map((o, i) =>
                e(
                  "div",
                  { key: i, className: "rounded-lg border border-white/[0.06] px-2.5 py-1.5" },
                  e(
                    "div",
                    { className: "flex flex-wrap items-center gap-1.5 text-[11.5px]" },
                    e("span", { className: "font-medium text-slate-100" }, o.sku || "—"),
                    o.market ? e("span", { className: "text-slate-400" }, `@ ${o.market}`) : null,
                    o.opportunity_score != null
                      ? e("span", { className: "ml-auto text-[10px] tabular-nums text-sky-200" }, `机会分 ${o.opportunity_score}`)
                      : null,
                  ),
                  e(
                    "div",
                    { className: "mt-0.5 text-[10px] leading-relaxed text-slate-500" },
                    [o.persona && `人群:${o.persona}`, o.content_angle && `角度:${o.content_angle}`, o.basis && `依据:${o.basis}`]
                      .filter(Boolean)
                      .join(" · ") || "—",
                  ),
                ),
              ),
            ),
      ),
      e(
        Card,
        { title: "③ 建议行动", hint: "action inbox suggested + 超期寄样 + 待深析 —— 人审按钮 GTM-3 接线" },
        e(ActionsBlock, {
          items: summary.recommended_actions.items,
          note: summary.recommended_actions.note,
          emptyText: "暂无建议行动(诚实空态)。",
        }),
      ),
      e(
        Card,
        { title: "④ 策略模拟入口", hint: summary.strategy_defaults.note || "选 SKU + 预算,三种花法并排对比(零 LLM 决定性模拟)" },
        summary.strategy_defaults.sku_hint
          ? e(
              "div",
              { className: "mb-2 text-[11px] text-slate-400" },
              `建议入口:${summary.strategy_defaults.sku_hint}`,
              summary.strategy_defaults.budget_hint != null ? ` · 预算参考 $${summary.strategy_defaults.budget_hint}` : "",
            )
          : null,
        e(StrategySimPanel, { apiToken }),
      ),
      e(
        Card,
        { title: "⑤ 复盘学习", hint: "prediction ledger + 周记分卡 + 失误复盘(全只读)" },
        e(LearningBlock, { digest, footnote: "全局口径" }),
      ),
      summary.generated_at
        ? e("div", { className: "text-right text-[10px] text-slate-600" }, `生成于 ${summary.generated_at}(UTC)· 纯聚合已有数据,零采集零写库`)
        : null,
    );

  // ---- SKU 作战预览五区块 ----
  const planCards = p &&
    e(
      "div",
      { className: "space-y-4" },
      // ① 主判断
      e(
        Card,
        {
          title: `① 主判断 · ${selectedSku}`,
          hint: "该不该推 / 押哪个市场 / 主打人群 / 走哪条主线",
          extra: confBadge(p.thesis.confidence),
        },
        p.thesis.status && p.thesis.status !== "ok" && p.thesis.status !== "ready"
          ? e(Empty, { text: `主判断暂不可用(${p.thesis.status})。` })
          : e(
              "div",
              null,
              e(
                "div",
                { className: "grid grid-cols-1 gap-2 md:grid-cols-2" },
                ([
                  ["该不该推", p.thesis.go_nogo],
                  ["优先市场", p.thesis.market],
                  ["主打人群", p.thesis.persona],
                  ["主线打法", p.thesis.mainline],
                ] as Array<[string, string]>).map(([label, value], i) =>
                  e(
                    "div",
                    { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
                    e("div", { className: "text-[10px] text-slate-500" }, label),
                    e("div", { className: "mt-0.5 text-[13px] font-medium text-white" }, value || "—"),
                  ),
                ),
              ),
              p.thesis.basis_summary
                ? e("div", { className: "mt-2 text-[11px] leading-relaxed text-slate-400" }, `判断依据:${p.thesis.basis_summary}`)
                : null,
              e(
                "div",
                { className: "mt-3" },
                e("div", { className: "mb-1.5 text-[10px] text-slate-500" }, "市场机会(赛道口径)"),
                e(SectionList, { section: p.market_opportunity, emptyText: "赛道段无数据(诚实空态)。", max: 4 }),
              ),
            ),
      ),
      // ② 条件化预判
      e(
        Card,
        {
          title: "② 市场预判(条件化)",
          hint: "每条都是「条件成立才行动」的情景,不是结果承诺;触发加码与撤退线缺一不可",
          extra: e(
            "span",
            { className: "shrink-0 rounded-md border border-purple-300/30 bg-purple-500/[0.12] px-2 py-0.5 text-[10px] text-purple-100" },
            "条件 ≠ 断言",
          ),
        },
        p.forecast.length === 0
          ? e(Empty, { text: "预判段暂无内容(样本不足或端点未就绪,诚实空态)。" })
          : e(
              "div",
              { className: "grid grid-cols-1 gap-2 lg:grid-cols-3" },
              p.forecast.slice(0, 6).map((f, i) =>
                e(
                  "div",
                  { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
                  e(
                    "div",
                    { className: "flex items-center justify-between gap-2" },
                    e("span", { className: "text-[11px] font-semibold text-slate-200" }, f.horizon_days != null ? `${f.horizon_days} 天窗口` : "窗口 —"),
                    confBadge(f.confidence),
                  ),
                  e(
                    "div",
                    { className: "mt-2 space-y-1.5 text-[10.5px] leading-relaxed" },
                    e(
                      "div",
                      { className: "border-l-2 border-sky-300/50 pl-2" },
                      e("span", { className: "text-slate-500" }, "预判:"),
                      e("span", { className: "text-slate-200" }, f.statement || "—"),
                    ),
                    e(
                      "div",
                      { className: "border-l-2 border-slate-400/40 pl-2" },
                      e("span", { className: "text-slate-500" }, "依据:"),
                      e("span", { className: "text-slate-300" }, f.signals_summary || "—"),
                    ),
                    e(
                      "div",
                      { className: "border-l-2 border-emerald-300/50 pl-2" },
                      e("span", { className: "text-slate-500" }, "触发加码:"),
                      f.escalate_if
                        ? e("span", { className: "text-emerald-100" }, f.escalate_if)
                        : e("span", { className: "text-rose-300" }, "条件缺失(不合规,待后端补齐)"),
                    ),
                    e(
                      "div",
                      { className: "border-l-2 border-rose-300/50 pl-2" },
                      e("span", { className: "text-slate-500" }, "撤退条件:"),
                      f.retreat_if
                        ? e("span", { className: "text-rose-100" }, f.retreat_if)
                        : e("span", { className: "text-rose-300" }, "条件缺失(不合规,待后端补齐)"),
                    ),
                  ),
                  // U3 阈值进度条:文本里解析得出量化值才出条(解析不出保持纯文本卡,不硬编)。
                  e(ThresholdBar, { escalateIf: f.escalate_if, retreatIf: f.retreat_if }),
                ),
              ),
            ),
      ),
      // ③ 增长路线图
      e(
        Card,
        { title: "③ 增长路线图", hint: "W1 / W2-4 / M2-3 三段推进,每段列 KOL·Dealer·官号·自媒体·独立站怎么配合" },
        p.roadmap.length === 0
          ? e(Empty, { text: "端点未返回 roadmap 段 —— 前端不编造节奏,渠道配合先看下方渠道段(诚实空态)。" })
          : e(
              "div",
              { className: "grid grid-cols-1 gap-2 md:grid-cols-3" },
              p.roadmap.map((ph) =>
                e(
                  "div",
                  { key: ph.key, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
                  e("div", { className: "text-[11px] font-semibold text-sky-200" }, ph.label),
                  ph.channels.length > 0
                    ? e(
                        "div",
                        { className: "mt-1.5 space-y-1" },
                        ph.channels.map((c, j) =>
                          e(
                            "div",
                            { key: j, className: "flex gap-1.5 text-[10.5px] leading-relaxed" },
                            e("span", { className: "shrink-0 rounded border border-white/[0.08] px-1.5 py-0.5 text-[9px] text-slate-400" }, c.channel || "—"),
                            e("span", { className: "min-w-0 text-slate-300" }, c.play || "—"),
                          ),
                        ),
                      )
                    : null,
                  ph.items.length > 0
                    ? e(
                        "ul",
                        { className: "mt-1.5 list-disc space-y-0.5 pl-4 text-[10.5px] leading-relaxed text-slate-300" },
                        ph.items.slice(0, 6).map((s, j) => e("li", { key: j }, s)),
                      )
                    : null,
                  ph.channels.length === 0 && ph.items.length === 0
                    ? e("div", { className: "mt-1.5 text-[10px] text-slate-600" }, ph.note || "该段暂无安排(诚实空态)。")
                    : ph.note
                      ? e("div", { className: "mt-1.5 text-[9.5px] text-slate-600" }, ph.note)
                      : null,
                ),
              ),
            ),
        // 渠道段:KOL 候选(风险只出标签)/ Dealer / 官号 / 独立站 / 内容角度
        e(
          "div",
          { className: "mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3" },
          (
            [
              ["KOL 候选", p.kol_candidates, "候选池无匹配 KOL(诚实空态)。"],
              ["Dealer 铺货", p.dealer_targets, "Dealer 数据未导入(GTM-2 激活)。"],
              ["官号动作", p.official_channel_actions, "官号快照无近期表现数据。"],
              ["独立站承接", p.shopify_indie_site_actions, "独立站段无建议(本地无订单,诚实标注)。"],
              ["内容角度", p.content_angles, "暂无内容角度建议。"],
            ] as Array<[string, GtmPlanSection, string]>
          ).map(([label, section, emptyText], i) =>
            e(
              "div",
              { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
              e("div", { className: "mb-1.5 text-[10.5px] font-semibold text-slate-300" }, label),
              e(SectionList, { section, emptyText, max: 4 }),
            ),
          ),
        ),
      ),
      // ④ 今日行动
      e(
        Card,
        { title: "④ 今日行动", hint: "materialize 预览,不落库;每条带六要素,人审执行按钮 GTM-3 接线" },
        e(
          "div",
          { className: "grid grid-cols-1 gap-3 xl:grid-cols-2" },
          e(
            "div",
            null,
            e(ActionsBlock, { items: p.action_inbox_items, emptyText: "本次预览未产出行动项(诚实空态)。" }),
          ),
          e(
            "div",
            { className: "space-y-3" },
            e(
              "div",
              { className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
              e("div", { className: "mb-1.5 text-[10.5px] font-semibold text-slate-300" }, "预算分配(三档模板)"),
              e(SectionList, { section: p.budget_mix, emptyText: "预算段无内容(诚实空态)。", max: 4 }),
            ),
            e(StrategySimPanel, { apiToken }),
          ),
        ),
      ),
      // ⑤ 复盘学习(全局口径复用,诚实标注非该 SKU 专属)
      e(
        Card,
        { title: "⑤ 复盘学习", hint: "prediction ledger + 周记分卡 + 失误复盘 —— 全局口径,非该 SKU 专属" },
        e(LearningBlock, { digest, footnote: "复用全局复盘账本(preview 无 SKU 级学习段,诚实标注)" }),
      ),
      // 风险 / 数据缺口 / 成功指标 / 覆盖度脚注
      e(
        "div",
        { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4" },
        p.risks.length > 0 &&
          e(
            "div",
            { className: "mb-2 flex flex-wrap items-center gap-1.5" },
            e("span", { className: "text-[10px] text-slate-500" }, "风险:"),
            p.risks.slice(0, 8).map((r, i) =>
              e("span", { key: i, className: "rounded border border-amber-300/25 bg-amber-500/[0.08] px-2 py-0.5 text-[10px] text-amber-200" }, r),
            ),
          ),
        (p.data_gaps.length > 0 || (meta?.data_gaps.length ?? 0) > 0) &&
          e(
            "div",
            { className: "mb-2 flex flex-wrap items-center gap-1.5" },
            e("span", { className: "text-[10px] text-slate-500" }, "数据缺口:"),
            Array.from(new Set([...p.data_gaps, ...(meta?.data_gaps || [])])).slice(0, 10).map((g, i) =>
              e("span", { key: i, className: "rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-400" }, g),
            ),
          ),
        p.success_metrics.length > 0 &&
          e(
            "div",
            { className: "mb-2" },
            e("div", { className: "mb-1 text-[10px] text-slate-500" }, "成功指标(规则库口径,可被自有数据推翻)"),
            e(
              "div",
              { className: "flex flex-wrap gap-1.5" },
              p.success_metrics.slice(0, 8).map((m, i) =>
                e(
                  "span",
                  { key: i, className: "rounded border border-sky-300/20 bg-sky-500/[0.06] px-2 py-0.5 text-[10px] text-sky-100" },
                  `${m.metric || "—"}${m.threshold ? ` ≥ ${m.threshold}` : ""}`,
                  m.confidence ? e("span", { className: "ml-1 text-sky-300/70" }, `(置信 ${m.confidence})`) : null,
                ),
              ),
            ),
          ),
        e(
          "div",
          { className: "text-[10px] leading-relaxed text-slate-600" },
          `生成于 ${meta?.generated_at || "—"}(UTC)· 纯读预览零写库零 LLM 零采集 · 外部信号来源:内部缓存/计划口径,外部雷达 GTM-5 待接入`,
          meta && Object.keys(meta.coverage).length > 0
            ? ` · 覆盖度:${Object.entries(meta.coverage)
                .slice(0, 6)
                .map(([k, v]) => `${k}=${typeof v === "string" || typeof v === "number" ? v : "…"}`)
                .join(" / ")}`
            : "",
        ),
      ),
    );

  return e(
    "div",
    { className: "mx-auto max-w-6xl space-y-4 p-4 md:p-6" },
    header,
    controls,
    // U3 · 90 天北极星三表盘(自拉取自判空;全局/预览两模式都挂顶部)
    apiToken ? e(NorthStarGauges, { apiToken }) : null,
    !apiToken && e(Empty, { text: "未登录 / 无 token,无法加载数据。" }),
    planError && e("div", { className: "rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200" }, planError),
    planLoading && e(PreviewSkeleton),
    // 预览模式:五区块
    !planLoading && planCards,
    // 全局模式:五卡
    !planLoading && !plan && apiToken &&
      e(
        "div",
        null,
        summaryLoading && e("div", { className: "py-8 text-center text-[12px] text-slate-400" }, "全局摘要加载中…"),
        !summaryLoading && summaryError &&
          e("div", { className: "rounded-lg border border-amber-300/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-200" },
            `全局摘要暂不可用:${summaryError}(端点 W1 在建,选 SKU 生成预览不受影响)`),
        !summaryLoading && !summaryError && globalCards,
      ),
  );
}
