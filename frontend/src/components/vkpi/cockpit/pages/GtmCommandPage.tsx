import React from "react";
import { Compass } from "lucide-react";
import { StrategySimPanel } from "../components/StrategySimPanel";
import { NorthStarGauges } from "../components/NorthStarGauges";
// W4 · 渠道四面板孤儿布线(均自取数自判空,失败安静缺席)。
import { DealerFitPanel } from "../components/DealerFitPanel";
import { OfficialPlannerPanel } from "../components/OfficialPlannerPanel";
import { IndieSitePanel } from "../components/IndieSitePanel";
import { ChannelMixPanel } from "../components/ChannelMixPanel";
import { usePermissions } from "../../../../hooks/usePermissions";
// 千行卫兵抽段:共享渲染基元 + planCards 区块 ①②③/脚注(照 MyKolPage.Sections.tsx 先例)。
import {
  ActionsBlock,
  Card,
  Empty,
  LearningBlock,
  PlanFootnote,
  PlanForecastCard,
  PlanRoadmapCard,
  PlanThesisCard,
  PreviewSkeleton,
  SectionList,
  confBadge,
} from "./GtmCommandPage.Sections";
// Gate D · 增长指挥台四层(健康条 + 路线/执行/学习/Bet 生命周期);全部纯读、失败安静。
import { CommandDeck, HealthStrip } from "./GtmCommandPage.Command";
import {
  getGtmPlanPreview,
  getMarketBrainSummary,
  listSkuOptions,
  materializeGtmPlan,
} from "../../../../services/vkpi/gtmCommand-api";
import type {
  GtmGoal,
  GtmMaterializeResult,
  GtmPlanPreview,
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
//   逐条人审执行按钮 v1 仍占位 disabled(title「GTM-3 接线」,per-item 执行在 Action Inbox 做);
//   ④ 今日行动已接「生成行动」流:dry-run 预演(零写库)→ 确认落库(materialize,逐条
//   requires_approval 进 Action Inbox 人审)。失败安静:每卡自判空,单卡错不拖垮整页。

const e = React.createElement;

const GOAL_OPTIONS: Array<{ value: GtmGoal; label: string }> = [
  { value: "exposure", label: "曝光" },
  { value: "conversion", label: "转化" },
  { value: "content", label: "素材" },
  { value: "channel", label: "渠道铺货" },
];

export function GtmCommandPage({ apiToken = "", onNavigate }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
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
  // ④ 今日行动 · 生成行动流(materialize:dry-run 预演 → 确认落库)
  const [matPreview, setMatPreview] = React.useState<GtmMaterializeResult | null>(null);
  const [matDone, setMatDone] = React.useState<GtmMaterializeResult | null>(null);
  const [matBusy, setMatBusy] = React.useState<"" | "dry" | "persist">("");
  const [matError, setMatError] = React.useState("");

  // 真落库按钮可见性:用现成 manager/owner 判定(usePermissions.isManager,不自造权限体系)。
  // 独立挂载 / 单测无 AuthProvider 时 useAuth 会抛 —— 兜底放行按钮,由后端 vkpi:write 403 把关
  // 并走 matError 提示。usePermissions 内部仅 useContext,每次渲染同序调用,try 包裹不破坏 hook 顺序。
  let canPersistBets = true;
  try {
    canPersistBets = usePermissions().isManager();
  } catch {
    canPersistBets = true; // 无 Auth 上下文 → 后端 403 兜底
  }

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
      // 换 plan 即作废旧的生成行动预演/回执(对账跟着 plan 走)。
      setMatPreview(null);
      setMatDone(null);
      setMatError("");
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

  // 生成行动流:dryRun=true 预演(零写库,幂等对账);dryRun=false 真落库(bet 进
  // Action Inbox 逐条 requires_approval 人审)。失败只写 matError 小条,不拖垮整页。
  const runMaterialize = React.useCallback(
    (dryRun: boolean) => {
      if (!apiToken || !selectedSku || matBusy) return;
      setMatBusy(dryRun ? "dry" : "persist");
      setMatError("");
      const budget = Number(budgetText);
      materializeGtmPlan(
        apiToken,
        {
          sku: selectedSku,
          country,
          budgetUsd: Number.isFinite(budget) && budget > 0 ? budget : 3000,
          goal,
          windowDays,
        },
        dryRun,
      )
        .then((res) => {
          if (res.status === "error" || (!dryRun && !res.ok)) {
            setMatError(res.reason || "生成行动失败");
            return;
          }
          if (dryRun) {
            setMatPreview(res);
            setMatDone(null);
          } else {
            setMatDone(res);
            setMatPreview(null);
          }
        })
        .catch((err: any) =>
          setMatError(String(err?.detail || err?.message || (dryRun ? "行动预演失败" : "行动落库失败"))),
        )
        .finally(() => setMatBusy(""));
    },
    [apiToken, selectedSku, matBusy, budgetText, country, goal, windowDays],
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
          onClick: () => { setPlan(null); setPlanError(""); setSelectedSku(""); setMatPreview(null); setMatDone(null); setMatError(""); },
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
      // ① 主判断(抽段:GtmCommandPage.Sections.PlanThesisCard)
      e(PlanThesisCard, { selectedSku, p }),
      // ② 条件化预判(抽段:PlanForecastCard)
      e(PlanForecastCard, { p }),
      // ③ 增长路线图 + 渠道段五宫格(抽段:PlanRoadmapCard)
      e(PlanRoadmapCard, { p }),
      // ③.5 · W4 渠道四面板(Dealer 适配 / 官号计划器 / 独立站承接 / 渠道组合)。
      // 四组件全部自取数自判空(诚实空态),接口失败安静缺席 —— 单卡错不拖垮整页宪法。
      e(
        "div",
        { className: "grid grid-cols-1 gap-4 xl:grid-cols-2" },
        e(DealerFitPanel, { apiToken, sku: selectedSku }),
        e(OfficialPlannerPanel, { apiToken, sku: selectedSku }),
        e(IndieSitePanel, { apiToken, sku: selectedSku }),
        e(ChannelMixPanel, {
          apiToken,
          sku: selectedSku,
          budget: Number(budgetText) > 0 ? Number(budgetText) : 3000,
          goal,
        }),
      ),
      // ④ 今日行动
      e(
        Card,
        { title: "④ 今日行动", hint: "「生成行动」两步:预演(dry-run 零写库,幂等对账)→ 确认落库(bet 进 Action Inbox 逐条人审);逐条人审执行按钮 GTM-3 接线" },
        // 生成行动流:按钮① dry-run 预演 → 摘要 + 条目预览;按钮② 确认落库(manager/owner 可见,
        // 后端 vkpi:write 403 兜底);失败只出本块小条(单卡错不拖垮整页)。
        e(
          "div",
          { className: "mb-3 rounded-xl border border-sky-300/15 bg-sky-500/[0.04] p-3" },
          e(
            "div",
            { className: "flex flex-wrap items-center gap-2" },
            e(
              "button",
              {
                onClick: () => runMaterialize(true),
                disabled: !selectedSku || matBusy !== "",
                title: "dry-run:只出 bet 预览与幂等对账,零写库",
                className:
                  "rounded-lg border border-sky-300/30 bg-sky-500/[0.12] px-2.5 py-1.5 text-[11px] text-sky-100 hover:bg-sky-500/[0.2] disabled:cursor-not-allowed disabled:opacity-40",
              },
              matBusy === "dry" ? "预演中…" : "生成行动(预演)",
            ),
            matPreview && canPersistBets
              ? e(
                  "button",
                  {
                    onClick: () => runMaterialize(false),
                    disabled: matBusy !== "" || (matPreview.would_insert ?? 0) === 0,
                    title:
                      (matPreview.would_insert ?? 0) === 0
                        ? "本次无可新增 bet(已存在的幂等不重插)"
                        : "真落库:bet 进 Action Inbox(status=suggested),逐条 requires_approval 人审;幂等不重插",
                    className:
                      "rounded-lg border border-emerald-300/30 bg-emerald-500/[0.12] px-2.5 py-1.5 text-[11px] text-emerald-100 hover:bg-emerald-500/[0.2] disabled:cursor-not-allowed disabled:opacity-40",
                  },
                  matBusy === "persist" ? "落库中…" : `确认落库(新增 ${matPreview.would_insert ?? 0} 条)`,
                )
              : null,
            e(
              "span",
              { className: "text-[9.5px] text-slate-500" },
              "预演零写库 · 落库后逐条进 Action Inbox 人审,无自动执行",
            ),
          ),
          matError
            ? e(
                "div",
                { className: "mt-2 rounded-lg border border-rose-300/25 bg-rose-500/[0.06] px-2.5 py-1.5 text-[10.5px] text-rose-200" },
                `生成行动未生效 · ${matError}`,
              )
            : null,
          matPreview
            ? e(
                "div",
                { className: "mt-2" },
                e(
                  "div",
                  { className: "text-[10.5px] text-slate-300" },
                  `预演对账:共 ${matPreview.bets_total} 条 bet · 可新增 ${matPreview.would_insert ?? 0} 条 · 已存在 ${matPreview.already_present} 条(幂等不重插)` +
                    (matPreview.skipped_incomplete > 0 ? ` · 跳过不完整 ${matPreview.skipped_incomplete} 条` : ""),
                ),
                matPreview.bets.length > 0
                  ? e(
                      "ul",
                      { className: "mt-1.5 list-disc space-y-0.5 pl-4 text-[10px] leading-relaxed text-slate-400" },
                      matPreview.bets.slice(0, 6).map((b, i) => e("li", { key: i }, b.title || b.dedupe_key || "—")),
                      matPreview.bets.length > 6
                        ? e("li", { key: "more", className: "list-none text-slate-600" }, `… 共 ${matPreview.bets.length} 条`)
                        : null,
                    )
                  : e("div", { className: "mt-1.5 text-[10px] text-slate-500" }, "本次 plan 无完整七要素 bet 可落(诚实空态)。"),
              )
            : null,
          matDone
            ? e(
                "div",
                { className: "mt-2 rounded-lg border border-emerald-300/25 bg-emerald-500/[0.07] px-2.5 py-1.5" },
                e(
                  "div",
                  { className: "text-[10.5px] text-emerald-200" },
                  `已落库:新增 ${matDone.inserted_new ?? 0} 条 · 已存在 ${matDone.already_present} 条未重插 · 逐条 requires_approval 待人审`,
                ),
                e(
                  "button",
                  {
                    onClick: () => onNavigate?.("dashboard"),
                    className:
                      "mt-1.5 rounded-lg border border-emerald-300/30 bg-emerald-500/[0.12] px-2.5 py-1 text-[10px] text-emerald-100 hover:bg-emerald-500/[0.2]",
                  },
                  "去 Action Inbox 人审 →",
                ),
              )
            : null,
        ),
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
      // 风险 / 数据缺口 / 成功指标 / 覆盖度脚注(抽段:PlanFootnote)
      e(PlanFootnote, { p, meta }),
    );

  return e(
    "div",
    { className: "mx-auto max-w-6xl space-y-4 p-4 md:p-6" },
    header,
    controls,
    // Gate D · 顶部健康条(SHA/迁移/Worker/调度/留痕 五点;纯读失败安静)。
    apiToken ? e(HealthStrip, { apiToken }) : null,
    // U3 · 90 天北极星三表盘(自拉取自判空;全局/预览两模式都挂顶部)
    apiToken ? e(NorthStarGauges, { apiToken }) : null,
    // Gate D · 增长指挥台(路线/执行/学习/Bet):界面只展示决策与状态,聪明藏系统内。
    //   全局态吃 summary,preview 态吃 plan;深度分析卡片仍在下方(既有能力零回退)。
    apiToken
      ? e(CommandDeck, {
          apiToken,
          summary,
          plan,
          selectedSku,
          budgetText,
          goal,
          digest,
          onPickSku: loadPreview,
          onNavigate,
        })
      : null,
    !apiToken && e(Empty, { text: "未登录 / 无 token,无法加载数据。" }),
    // ↓↓↓ 深度分析(既有五卡 / 五区块保留为下钻层)↓↓↓
    apiToken
      ? e(
          "div",
          { className: "flex items-center gap-2 pt-1" },
          e("span", { className: "text-[11px] font-medium text-slate-500" }, "深度分析"),
          e("span", { className: "h-px flex-1 bg-white/[0.06]" }),
        )
      : null,
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
