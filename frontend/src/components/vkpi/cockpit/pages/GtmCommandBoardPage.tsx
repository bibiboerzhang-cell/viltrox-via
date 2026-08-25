import React from "react";
import { PencilLine, RefreshCw } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { EmbeddedDashboardModule } from "../components/EmbeddedDashboardModule";
import { usePermissions } from "../../../../hooks/usePermissions";
import { useCachedGet } from "../../../../hooks/useCachedGet";
import { ErrorCard, LoadingLine, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import { ModuleProvModal } from "./MarketVoicePage.dialogs";
import {
  BetsBody,
  GTM_PROV_TITLES,
  GtmKpiBand,
  LearnBody,
  MODULE_SOURCES,
  OppsBody,
  QueueBody,
  RouteBody,
  SignalsBody,
} from "./GtmCommandBoardPage.modules";
import {
  ActionsList,
  FootnoteBody,
  ForecastBody,
  PlanPending,
  RoadmapBody,
  ThesisBody,
  TodayActionsBody,
  type MaterializeAdapter,
} from "./GtmCommandBoardPage.sections";
import {
  DealerEmbed,
  HealthEmbed,
  IndieEmbed,
  MixEmbed,
  NorthstarEmbed,
  OfficialPlanEmbed,
  SimEmbed,
} from "./GtmCommandBoardPage.embeds";
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
import type { ActionInboxResponse } from "../../../../services/vkpi/actionInbox-api";

// GTM Command → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage
//   五件套 embeds 手法 1:1 同构)。旧 GtmCommandPage 族保留不删(回滚垫);切换点
//   只在 CockpitApp.tsx 的 lazy import 一行(报告注明,本刀不改 CockpitApp)。
//   页壳:pagehead(标题 + SKU 徽 + 实时辉光点 + 刷新 + 编辑布局)+ 作战控件行
//   (SKU 选择器 / 国家 / 预算 / 目标 / 窗口 / 生成作战预览 / 返回全局)+
//   EditableDashboardBoard(20 模块注册表 + 默认八行 + palette 备选)。
//   数据源(全真,零编造;行数 2026-07-12 只读 PG 实测):
//     GET  /api/admin/vkpi/market-brain/summary          —— 全局五段聚合
//     GET  /api/admin/vkpi/market-brain/gtm-plan/preview —— SKU 作战预览(public_plan)
//     POST /api/admin/vkpi/market-brain/gtm-plan/materialize —— 唯一写路径(dry-run
//          默认零写库;真落库逐条 requires_approval 进 Action Inbox 人审)
//     GET  /api/admin/vkpi/actions/inbox?limit=50        —— KPI 带 + 执行队列同请求
//          同缓存(useCachedGet,与旧 ExecutionQueueLayer 同 path 零重复拉取)
//     GET  /api/admin/vkpi/sku/list · /health · scheduler-tasks · runtime/metrics ·
//          /gtm/northstar · /channel/* · /strategy/simulate(embeds 内自取)
//   【总脑纯读红线】绝不调用 marketing-brain/daily 与 market/trends 的 GET(两端点
//   有隐藏写入,GTM-1 规格明令禁碰;全族已核实绕开)。
//   【验收纪律】卡面零介绍性文案:旧页副题/卡 hint/方法论句全部收进 MODULE_SOURCES
//   (SrcChip + 溯源弹窗),按钮动词直说;每个数字有真源,空态诚实短句。
// 红线:纯展示 + 唯一写路径 materialize(人审同向);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 零写死色;零 opacity 修饰类;布局只走本机 storageKey,不传 apiToken 给
//   EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1 键,金样板同注释)。

const STORAGE_KEY = "vkpi-gtm-command-layout-v1";

// 默认布局(12 列 · 八行):kpiG(12) → route(8)+health(4) → signals(8)+queue(4)
// → thesis(8)+northstar(4) → forecast(12) → roadmap(12) → todayActions(8)+bets(4) → learn(12)
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiG", span: 12 },
  { moduleKey: "route", span: 8 },
  { moduleKey: "health", span: 4 },
  { moduleKey: "signals", span: 8 },
  { moduleKey: "queue", span: 4 },
  { moduleKey: "thesis", span: 8 },
  { moduleKey: "northstar", span: 4 },
  { moduleKey: "forecast", span: 12 },
  { moduleKey: "roadmap", span: 12 },
  { moduleKey: "todayActions", span: 8 },
  { moduleKey: "bets", span: 4 },
  { moduleKey: "learn", span: 12 },
];

const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

const GOAL_OPTIONS: Array<{ value: GtmGoal; label: string }> = [
  { value: "exposure", label: "曝光" },
  { value: "conversion", label: "转化" },
  { value: "content", label: "素材" },
  { value: "channel", label: "渠道铺货" },
];

const EMPTY_DIGEST: LearningDigest = {
  validated: [],
  effective_styles: [],
  dropped_channels: [],
  next_change: "",
  honesty_note: "",
  status: "",
};

export function GtmCommandBoardPage({ apiToken = "", onNavigate, embeddedModuleKey }: { apiToken?: string; onNavigate?: (navKey: string) => void; embeddedModuleKey?: string }) {
  const [editing, setEditing] = React.useState(false);
  const [reloadTick, setReloadTick] = React.useState(0);
  const [provKey, setProvKey] = React.useState<string | null>(null);

  // 全局聚合(五段)
  const [summary, setSummary] = React.useState<MarketBrainSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = React.useState(false);
  const [summaryError, setSummaryError] = React.useState("");

  // SKU 选择器(/sku/list,与 SKU 360°/模拟器同源)
  const [query, setQuery] = React.useState("");
  const [options, setOptions] = React.useState<SkuListItem[]>([]);
  const [dropdownOpen, setDropdownOpen] = React.useState(false);
  const [searching, setSearching] = React.useState(false);
  const [selectedSku, setSelectedSku] = React.useState("");

  // 预览输入 + 结果
  const [country, setCountry] = React.useState("US");
  const [budgetText, setBudgetText] = React.useState("3000");
  const [goal, setGoal] = React.useState<GtmGoal>("conversion");
  const [windowDays, setWindowDays] = React.useState(30);
  const [plan, setPlan] = React.useState<GtmPlanPreview | null>(null);
  const [planLoading, setPlanLoading] = React.useState(false);
  const [planError, setPlanError] = React.useState("");

  // 生成行动流(materialize:dry-run 预演 → 确认落库)
  const [matPreview, setMatPreview] = React.useState<GtmMaterializeResult | null>(null);
  const [matDone, setMatDone] = React.useState<GtmMaterializeResult | null>(null);
  const [matBusy, setMatBusy] = React.useState<"" | "dry" | "persist">("");
  const [matError, setMatError] = React.useState("");

  // 真落库按钮可见性:manager/owner(usePermissions,不自造权限体系)。独立挂载 / 单测
  // 无 AuthProvider 时 useAuth 会抛 —— 兜底放行按钮,由后端 vkpi:write 403 把关(旧页同注释)。
  let canPersistBets = true;
  try {
    canPersistBets = usePermissions().isManager();
  } catch {
    canPersistBets = true;
  }

  // actions/inbox 近 50 条:KPI 带(待执行/增长押注)+ 执行队列共用一份(30s 缓存,
  // 与旧 ExecutionQueueLayer 同 path 同 ttl —— 切换新旧页零重复请求)。
  const inbox = useCachedGet<ActionInboxResponse>("/api/admin/vkpi/actions/inbox?limit=50", {
    token: apiToken,
    enabled: Boolean(apiToken),
    ttl: 30000,
  });

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSummaryLoading(true);
    setSummaryError("");
    getMarketBrainSummary(apiToken, { refresh: reloadTick > 0 })
      .then((res) => {
        if (!alive) return;
        if (res.status === "error") {
          setSummaryError(res.reason || "全局摘要刷新未完成");
          return;
        }
        setSummary(res);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setSummaryError(String(detail.detail || detail.message || "全局摘要加载失败"));
      })
      .finally(() => {
        if (alive) setSummaryLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, reloadTick]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setSearching(true);
    const timer = window.setTimeout(() => {
      listSkuOptions(apiToken, query)
        .then((items) => {
          if (alive) setOptions(items);
        })
        .catch(() => {
          if (alive) setOptions([]);
        })
        .finally(() => {
          if (alive) setSearching(false);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [apiToken, query]);

  const loadPreview = React.useCallback(
    (sku: string, refresh = false) => {
      if (!apiToken || !sku) return;
      setSelectedSku(sku);
      setDropdownOpen(false);
      setPlanLoading(true);
      setPlanError("");
      setPlan(null);
      // 换 plan 即作废旧的生成行动预演/回执(对账跟着 plan 走)
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
        refresh,
      })
        .then((res) => setPlan(res))
        .catch((err: unknown) => {
          const detail = (err as { detail?: unknown; message?: unknown }) || {};
          setPlanError(String(detail.detail || detail.message || "作战预览生成失败"));
        })
        .finally(() => setPlanLoading(false));
    },
    [apiToken, budgetText, country, goal, windowDays],
  );

  const clearPlan = React.useCallback(() => {
    setPlan(null);
    setPlanError("");
    setSelectedSku("");
    setMatPreview(null);
    setMatDone(null);
    setMatError("");
  }, []);

  // 生成行动流:dryRun=true 预演(零写库,幂等对账);dryRun=false 真落库(bet 进
  // Action Inbox 逐条 requires_approval 人审)。失败只写 matError,不拖垮整页。
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
          if (!res.ok || res.status === "error" || res.status === "scope_unavailable") {
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
        .catch((err: unknown) => {
          const detail = (err as { detail?: unknown; message?: unknown }) || {};
          setMatError(String(detail.detail || detail.message || (dryRun ? "行动预演失败" : "行动落库失败")));
        })
        .finally(() => setMatBusy(""));
    },
    [apiToken, selectedSku, matBusy, budgetText, country, goal, windowDays],
  );

  const p = plan?.public_plan;
  const previewReady = Boolean(plan && plan.status !== "scope_unavailable" && plan.status !== "error");
  const previewCount = (value: React.ReactNode): React.ReactNode | undefined => {
    if (plan?.status === "scope_unavailable") return "待接通";
    if (plan?.status === "error") return "失败";
    return previewReady ? value : undefined;
  };
  const digest: LearningDigest = summary?.learning_digest || EMPTY_DIGEST;

  const mat: MaterializeAdapter = {
    matPreview,
    matDone,
    matBusy,
    matError,
    canPersistBets,
    run: runMaterialize,
    onNavigate,
  };

  /* ---------- 卡头 props(金样板 cardProps 同构:SrcChip hover + 点击溯源弹窗) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const mergedRowsRef = React.useRef<Record<string, Array<[string, string]>>>({});
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    mergedRowsRef.current[key] = rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows, onOpenSrc: () => setProvKey(key) };
  };

  /* ---------- 闸(金样板双轨:未登录/加载/失败 → 诚实卡;绝不假数据) ---------- */
  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载 GTM 数据。
    </PendingCard>
  );

  const summaryGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (summaryLoading && !summary) return <LoadingLine text="全局聚合读取中…" />;
    if (summaryError && !summary) return <ErrorCard title="market-brain/summary 读取失败" text={summaryError} />;
    if (!summary) return <LoadingLine text="全局聚合读取中…" />;
    if (summary.status === "scope_unavailable") {
      return (
        <PendingCard>
          <b>当前租户的 GTM 聚合尚未接通</b> —— {summary.reason || "租户范围无法确认，已安全停止读取默认工作区数据。"}
        </PendingCard>
      );
    }
    return null;
  };

  // 作战预览模块闸:无 SKU → pending 短句;加载中 → LoadingLine;失败 → ErrorCard
  const planGate = (): React.ReactNode | null => {
    if (!apiToken) return noTokenCard;
    if (!selectedSku) return <PlanPending />;
    if (planLoading) return <LoadingLine text="作战预览聚合中…" />;
    if (planError) return <ErrorCard title="gtm-plan/preview 生成失败" text={planError} />;
    if (plan?.status === "scope_unavailable") {
      return (
        <PendingCard>
          <b>当前租户的 GTM 预览尚未接通</b> —— {plan.reason || "租户范围无法确认，已安全停止读取默认工作区数据。"}
        </PendingCard>
      );
    }
    if (plan?.status === "error") {
      return <ErrorCard title="gtm-plan/preview 生成失败" text={plan.reason || "作战预览返回错误状态。"} />;
    }
    if (!plan || !p) return <PlanPending />;
    return null;
  };

  /* ---------- 模块 render ---------- */

  const renderKpiBand = () => {
    const extraRows: Array<[string, string]> = summary?.generated_at
      ? [["生成于", `${summary.generated_at}(UTC 存 · 纯聚合零采集零写库)`]]
      : [];
    return (
      <ModuleCard {...cardProps("kpiG", "指挥台总览", summary ? "4 指标" : undefined, extraRows)}>
        {summaryGate() ?? <GtmKpiBand summary={summary!} inbox={inbox.data} inboxError={inbox.error} />}
      </ModuleCard>
    );
  };

  const renderRoute = () => {
    const cnt = plan?.status === "scope_unavailable"
      ? "待接通"
      : plan
        ? selectedSku
        : summary
          ? `${summary.product_opportunities.items.length} 机会`
          : undefined;
    const gate = selectedSku ? planGate() : summaryGate();
    return (
      <ModuleCard {...cardProps("route", "今日增长路线", cnt)}>
        {gate ?? (
          <RouteBody
            summary={summary}
            plan={selectedSku ? plan : null}
            selectedSku={selectedSku}
            budgetText={budgetText}
            goal={goal}
            onPickSku={selectedSku ? undefined : loadPreview}
          />
        )}
      </ModuleCard>
    );
  };

  const renderSignals = () => {
    const ws = summary?.weekly_signals;
    const extraRows: Array<[string, string]> = ws?.sources_note ? [["来源注", ws.sources_note]] : [];
    return (
      <ModuleCard {...cardProps("signals", "本周信号", ws ? `${ws.items.length}` : undefined, extraRows)}>
        {summaryGate() ?? <SignalsBody summary={summary!} />}
      </ModuleCard>
    );
  };

  const renderQueue = () => {
    const n = Array.isArray(inbox.data?.items) ? inbox.data!.items.length : null;
    return (
      <ModuleCard {...cardProps("queue", "今日执行队列", n != null ? `${n}` : undefined)}>
        {!apiToken ? noTokenCard : <QueueBody inbox={inbox.data} loading={inbox.loading} error={inbox.error} onNavigate={onNavigate} />}
      </ModuleCard>
    );
  };

  const renderLearn = () => {
    const extraRows: Array<[string, string]> = previewReady
      ? [["本页口径", "复用全局复盘账本 —— preview 无 SKU 级学习段,如实标注"]]
      : [];
    return (
      <ModuleCard {...cardProps("learn", "复盘学习", undefined, extraRows)}>
        {summaryGate() ?? <LearnBody digest={digest} />}
      </ModuleCard>
    );
  };

  const renderBets = () => {
    const gate = selectedSku ? planGate() : null;
    const cnt = previewCount("至「预览」") ?? "构想";
    return (
      <ModuleCard {...cardProps("bets", "Bet 生命周期", cnt)}>
        {!apiToken ? noTokenCard : gate ?? <BetsBody plan={selectedSku ? plan : null} />}
      </ModuleCard>
    );
  };

  const renderHealth = () => <HealthEmbed apiToken={apiToken} noToken={noTokenCard} />;
  const renderNorthstar = () => <NorthstarEmbed apiToken={apiToken} noToken={noTokenCard} />;
  const renderSim = () => (
    <SimEmbed
      apiToken={apiToken}
      skuHint={summary?.strategy_defaults.sku_hint}
      budgetHint={summary?.strategy_defaults.budget_hint}
      noToken={noTokenCard}
    />
  );

  /* ---------- 作战预览模块族(planGate 统一闸) ---------- */

  const renderThesis = () => (
    <ModuleCard {...cardProps("thesis", "主判断", previewCount(`${selectedSku} · 置信 ${p?.thesis.confidence || "—"}`))}>
      {planGate() ?? <ThesisBody p={p!} />}
    </ModuleCard>
  );

  const renderForecast = () => (
    <ModuleCard {...cardProps("forecast", "条件化预判", previewCount(`${p?.forecast.length || 0}`))}>
      {planGate() ?? <ForecastBody p={p!} />}
    </ModuleCard>
  );

  const renderRoadmap = () => (
    <ModuleCard {...cardProps("roadmap", "增长路线图", previewCount(`${p?.roadmap.length || 0} 段`))}>
      {planGate() ?? <RoadmapBody p={p!} />}
    </ModuleCard>
  );

  const renderTodayActions = () => (
    <ModuleCard {...cardProps("todayActions", "今日行动", previewCount(`${p?.action_inbox_items.length || 0}`))}>
      {planGate() ?? <TodayActionsBody p={p!} selectedSku={selectedSku} mat={mat} />}
    </ModuleCard>
  );

  const renderFootnote = () => (
    <ModuleCard {...cardProps("footnote", "风险与缺口", previewCount(`${p?.risks.length || 0} 风险`))}>
      {planGate() ?? <FootnoteBody p={p!} meta={plan?.meta} />}
    </ModuleCard>
  );

  /* ---------- palette 备选(全局细分) ---------- */

  const renderOpps = () => {
    const po = summary?.product_opportunities;
    return (
      <ModuleCard {...cardProps("opps", "产品机会", po ? `${po.items.length}` : undefined, po?.note ? [["备注", po.note]] : [])}>
        {summaryGate() ?? <OppsBody summary={summary!} />}
      </ModuleCard>
    );
  };

  const renderActions = () => {
    const ra = summary?.recommended_actions;
    return (
      <ModuleCard {...cardProps("actions", "建议行动", ra ? `${ra.items.length}` : undefined)}>
        {summaryGate() ?? <ActionsList items={summary!.recommended_actions.items} note={summary!.recommended_actions.note} emptyText="暂无建议行动。" />}
      </ModuleCard>
    );
  };

  const renderDealer = () => <DealerEmbed apiToken={apiToken} selectedSku={selectedSku} noToken={noTokenCard} />;
  const renderOfficialPlan = () => <OfficialPlanEmbed apiToken={apiToken} selectedSku={selectedSku} noToken={noTokenCard} />;
  const renderIndie = () => <IndieEmbed apiToken={apiToken} selectedSku={selectedSku} noToken={noTokenCard} />;
  const renderMix = () => <MixEmbed apiToken={apiToken} selectedSku={selectedSku} budgetText={budgetText} goal={goal} noToken={noTokenCard} />;

  /* ---------- 模块注册表(palette 全量可选;默认八行) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiG", label: "指挥台总览", description: "本周信号 / 产品机会 / 待执行动作 / 增长押注 四真数", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "route", label: "今日增长路线", description: "全局 Top 机会一键生成路线;预览态五段作战链", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 18, render: renderRoute },
    { key: "health", label: "系统健康", description: "版本 / 迁移 / Worker / 调度 / 留痕 五点纯读", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 5, minHeight: 3, maxHeight: 10, render: renderHealth },
    { key: "signals", label: "本周信号", description: "库内信号逐条 · freshness / 样本 / 置信随行", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderSignals },
    { key: "queue", label: "今日执行队列", description: "近 50 条建议按类型聚合 · 点击跳 Action Inbox", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 9, minHeight: 4, maxHeight: 16, render: renderQueue },
    { key: "thesis", label: "主判断", description: "该不该推 / 市场 / 人群 / 主线 + 市场机会段", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 5, maxHeight: 24, render: renderThesis },
    { key: "northstar", label: "90 天北极星", description: "Launch Brief / Dealer / 裁决率 三表盘真库现查", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 12, minHeight: 5, maxHeight: 18, render: renderNorthstar },
    { key: "forecast", label: "条件化预判", description: "预判 / 依据 / 触发加码 / 撤退条件 四段式 + 阈值条", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 10, minHeight: 5, maxHeight: 20, render: renderForecast },
    { key: "roadmap", label: "增长路线图", description: "W1 / W2-4 / M2-3 三段 + 渠道五段", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 14, minHeight: 6, maxHeight: 28, render: renderRoadmap },
    { key: "todayActions", label: "今日行动", description: "生成行动:预演零写库 → 确认落库进人审;六要素行动行", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 6, maxHeight: 28, render: renderTodayActions },
    { key: "bets", label: "Bet 生命周期", description: "构想→沉淀七段进度 · 随人审点亮", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderBets },
    { key: "learn", label: "复盘学习", description: "验证结论 / 有效风格 / 不值渠道 + 下次怎么变", category: "核心模块", defaultSpan: 12, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderLearn },
    // ↓ palette 备选(不进默认布局)
    { key: "opps", label: "产品机会", description: "逐条含人群 / 角度 / 依据(路线卡的明细版)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 4, maxHeight: 20, render: renderOpps },
    { key: "actions", label: "建议行动", description: "全局六要素行动行(原因/证据/成本/风险/收益)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 24, render: renderActions },
    { key: "sim", label: "策略模拟器", description: "SKU + 预算 → 三策略并排对比", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 26, render: renderSim },
    { key: "dealer", label: "Dealer 适配", description: "geo / category 适配打分(数据未导入如实空)", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderDealer },
    { key: "officialPlan", label: "官号内容计划", description: "逐官号近期表现 → 建议形式", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderOfficialPlan },
    { key: "indie", label: "独立站承接", description: "承接就绪 checklist · 0 订单如实标注", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderIndie },
    { key: "mix", label: "渠道组合", description: "品牌 / 转化两层预算配比建议", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 4, maxHeight: 20, render: renderMix },
    { key: "footnote", label: "风险与缺口", description: "风险标签 / 数据缺口 / 成功指标 / 覆盖度", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderFootnote },
  ];

  if (embeddedModuleKey) {
    return <EmbeddedDashboardModule modules={modules} moduleKey={embeddedModuleKey} boardLabel="GTM Command" />;
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + SKU 徽 + 缓存口径 + 真刷新 + 编辑布局 */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">GTM Command</span>
          {selectedSku ? <span className={PH_BADGE}>{selectedSku}</span> : null}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            短时缓存
          </span>
          <button
            type="button"
            onClick={() => {
              setReloadTick((tick) => tick + 1);
              if (selectedSku) loadPreview(selectedSku, true);
            }}
            title="跳过常规短时缓存并重新聚合；5 秒内的重复刷新会合并"
            className="flex items-center gap-1.5 rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <RefreshCw size={13} />
            <span>刷新数据</span>
          </button>
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {/* 作战控件行:SKU / 国家 / 预算 / 目标 / 窗口 / 生成作战预览 / 返回全局 */}
      <div className="mb-4 flex flex-wrap items-start gap-2">
        <div className="relative min-w-[220px] flex-1">
          <input
            value={query}
            onChange={(ev) => {
              setQuery(ev.target.value);
              setDropdownOpen(true);
            }}
            onFocus={() => setDropdownOpen(true)}
            placeholder="搜索 SKU / 型号…"
            className="w-full rounded-xl border border-line bg-panel px-3 py-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent"
          />
          {dropdownOpen && (
            <div className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-xl border border-line bg-card shadow-xl">
              {searching && <div className="px-3 py-2 text-[11px] text-muted">搜索中…</div>}
              {!searching && options.length === 0 && <div className="px-3 py-2 text-[11px] text-muted">无匹配 SKU</div>}
              {!searching &&
                options.map((opt) => (
                  <button
                    key={opt.sku}
                    type="button"
                    onClick={() => loadPreview(opt.sku)}
                    className="flex w-full items-center justify-between gap-2 border-b border-line px-3 py-1.5 text-left last:border-0 hover:bg-panel"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[11px] text-ink-2">{opt.model_name || opt.marketing_name || opt.sku}</span>
                      <span className="block truncate text-[9.5px] text-muted">
                        {opt.sku}
                        {opt.mount ? ` · ${opt.mount}` : ""}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-muted">{opt.price_usd != null ? `$${opt.price_usd}` : ""}</span>
                  </button>
                ))}
            </div>
          )}
        </div>
        <input
          value={country}
          onChange={(ev) => setCountry(ev.target.value.toUpperCase().slice(0, 2))}
          title="国家(ISO 两位码;v1 只影响受众地域口径)"
          placeholder="US"
          className="w-[52px] rounded-xl border border-line bg-panel px-2 py-2 text-center text-[12px] text-ink outline-none focus:border-accent"
        />
        <input
          value={budgetText}
          onChange={(ev) => setBudgetText(ev.target.value.replace(/[^0-9.]/g, ""))}
          inputMode="decimal"
          title="预算(USD,默认 3000)"
          className="w-[84px] rounded-xl border border-line bg-panel px-2 py-2 text-right text-[12px] tabular-nums text-warn outline-none focus:border-accent"
        />
        <select
          value={goal}
          onChange={(ev) => setGoal(ev.target.value as GtmGoal)}
          title="目标主线"
          className="rounded-xl border border-line bg-card px-2 py-2 text-[12px] text-ink-2 outline-none focus:border-accent"
        >
          {GOAL_OPTIONS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
        <select
          value={String(windowDays)}
          onChange={(ev) => setWindowDays(Number(ev.target.value) || 30)}
          title="预判窗口(天)"
          className="rounded-xl border border-line bg-card px-2 py-2 text-[12px] text-ink-2 outline-none focus:border-accent"
        >
          {[7, 14, 30].map((d) => (
            <option key={d} value={String(d)}>
              {d} 天
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            if (selectedSku) loadPreview(selectedSku, true);
          }}
          disabled={!selectedSku || planLoading}
          title={selectedSku ? `重新生成 ${selectedSku} 的作战预览` : "先在左侧选一个 SKU"}
          className="rounded-xl border border-accent bg-accent-soft px-3 py-2 text-[12px] text-accent transition-colors disabled:cursor-not-allowed disabled:text-muted"
        >
          {planLoading ? "生成中…" : "生成作战预览"}
        </button>
        {plan && (
          <button
            type="button"
            onClick={clearPlan}
            className="rounded-xl border border-line bg-panel px-3 py-2 text-[12px] text-ink-2 transition-colors hover:text-ink"
          >
            返回全局
          </button>
        )}
      </div>

      {!apiToken && <div className="mb-3">{noTokenCard}</div>}
      {summaryError && summary ? (
        <div className="mb-3 rounded-[10px] border border-warn bg-warn-soft px-3 py-2 text-[11.5px] text-warn" role="status">
          本次刷新未完成，已保留上次可信数据：{summaryError}
        </div>
      ) : null}
      {planError && (
        <div className="mb-3">
          <ErrorCard title="gtm-plan/preview 生成失败" text={planError} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 模块溯源弹窗(SrcChip 点击):口径 = MODULE_SOURCES + 调用点动态行,零第二份 */}
      {provKey && (
        <ModuleProvModal
          title={GTM_PROV_TITLES[provKey] || provKey}
          caliber={mergedRowsRef.current[provKey] || srcOf(provKey).rows}
          onClose={() => setProvKey(null)}
        />
      )}
    </div>
  );
}
