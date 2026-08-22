import React from "react";
import {
  AlertTriangle,
  Boxes,
  BriefcaseBusiness,
  CalendarDays,
  CircleUserRound,
  Compass,
  Database,
  IdCard,
  LibraryBig,
  MapPin,
  MessageCircleMore,
  MessagesSquare,
  RadioTower,
  Rocket,
  ShieldCheck,
  ShoppingBag,
  Target,
  Users,
  type LucideIcon,
} from "lucide-react";
import { ActivityFeed } from "./components/ActivityFeed";
import { ActionInboxPanel } from "./components/ActionInboxPanel";
import { ActiveCampaignsCard } from "./components/ActiveCampaignsCard";
import { ContentCalendarCard } from "./components/ContentCalendarCard";
import { DashboardBoardLinkCard } from "./components/DashboardBoardLinkCard";
import { DashboardCommandCenter } from "./components/DashboardCommandCenter";
import { DashboardMemoCard } from "./components/DashboardMemoCard";
import { DashboardTaskQueueCard } from "./components/DashboardTaskQueueCard";
import { DashboardTrendCard } from "./components/DashboardTrendCard";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "./components/EditableDashboardBoard";
import { KolFunnelCard } from "./components/KolFunnelCard";
import { MetricCard } from "./components/MetricCard";
import { NorthStarGauges } from "./components/NorthStarGauges";
import { OpsHealthCard } from "./components/OpsHealthCard";
import { SignalsAlertsCard } from "./components/SignalsAlertsCard";
import { TodayFocusStrip } from "./components/TodayFocusStrip";
import { TopMoversCard } from "./components/TopMoversCard";
import { TrendPulseBar } from "./components/TrendPulseBar";
import { UpcomingEventsCard } from "./components/UpcomingEventsCard";
import { KPI_SCOPES } from "./data/kpiScopes";
import { VIEW_MODES } from "./data/viewModes";
import { useT } from "./lib/i18n";
import { CROSS_BOARD_SOURCES, buildCrossBoardModules } from "./pages/crossBoardModules";

const e = React.createElement;
const SHOW_EXPERIMENTAL = String((import.meta as any).env?.VITE_EXPERIMENTAL_NAV ?? "") === "1";
const AIIntelligenceCard = React.lazy(() => import("./components/AIIntelligenceCard").then((module) => ({ default: module.AIIntelligenceCard })));
const BrandPulsePanel = React.lazy(() => import("./components/BrandPulsePanel").then((module) => ({ default: module.BrandPulsePanel })));
const CommentOpportunitiesPanel = React.lazy(() => import("./components/CommentOpportunitiesPanel").then((module) => ({ default: module.CommentOpportunitiesPanel })));
const GiftedFunnelPanel = React.lazy(() => import("./components/GiftedFunnelPanel").then((module) => ({ default: module.GiftedFunnelPanel })));
const MorningBriefCard = React.lazy(() => import("./components/MorningBriefCard").then((module) => ({ default: module.MorningBriefCard })));
const SemanticRecallCard = React.lazy(() => import("./components/SemanticRecallCard").then((module) => ({ default: module.SemanticRecallCard })));
const WorkerDevicesPanel = React.lazy(() => import("./components/WorkerDevicesPanel").then((module) => ({ default: module.WorkerDevicesPanel })));

function renderLazyModule(
  Component: React.LazyExoticComponent<React.ComponentType<any>>,
  props: Record<string, unknown>,
  fallbackLabel: string,
) {
  return e(React.Suspense, {
    fallback: e("div", { className: "vkpi-dashboard-lazy-module" }, fallbackLabel),
  }, e(Component, props));
}

const EMPTY_AI_INSIGHT = {
  confidence: 0,
  updatedLabel: "无信号",
  todayDecision: {
    text: "暂无可执行候选",
    amount: "--",
    reason: "当前没有可用真实候选",
    primaryAction: "查看证据",
    secondaryAction: "稍后处理",
  },
  strengthen: [],
  weaken: [],
  todayContent: [],
  poweredBy: "真实 API / 无信号",
};

const DEFAULT_LAYOUT = [
  { moduleKey: "today-focus", span: 12 },
  { moduleKey: "kpi", span: 12 },
  { moduleKey: "command-center", span: 8 },
  { moduleKey: "north-star", span: 4 },
  { moduleKey: "actions", span: 4 },
  { moduleKey: "signals", span: 4 },
  { moduleKey: "v6-fit", span: 4 },
  { moduleKey: "memo", span: 4 },
  { moduleKey: "trend-pulse", span: 8 },
  { moduleKey: "trend", span: 8 },
  { moduleKey: "ai-today", span: 4 },
];

// v5 safety modules are appended once to pre-v5 layouts. Once the stored
// envelope is v5, a user's explicit deletion remains authoritative.
const REQUIRED_V5_DEFAULT_MODULE_KEYS = ["today-focus", "trend-pulse"];

// 「业务板块」入口卡对应的导航键(palette 可见性按 canViewBoard 过滤)。顺序即 palette 顺序。
const BOARD_LINK_NAV_KEYS = [
  "my-kol", "kol-pool", "kolProfile", "projects", "events", "shopify", "dealers", "intelligent",
  "marketVoice", "sku360", "creativeLibrary", "replyQueue", "launchpad", "autonomy", "strategyBoard", "gtmCommand",
] as const;

function formatRoster(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : null;
}

const EMPTY_OBJECT: Record<string, never> = {};

/**
 * F1 · 模块定义 useMemo 化的配套:render 闭包只在 palette 依赖(语言/token/板块可见性)变化时重建,
 * 但渲染时必须吃到最新数据与最新 modal/popover setter(CockpitApp 每次状态变化都重传内联回调)。
 * latest ref 模式:父渲染时先写入最新值,子 render() 同一轮同步读取 —— 模块数组身份稳定,
 * EditableDashboardBoard 的 moduleMap/gridLayout/回调 memo 不再逐帧失效。
 */
function useLatestRef<T>(value: T): React.MutableRefObject<T> {
  const ref = React.useRef(value);
  ref.current = value;
  return ref;
}

/** 跨板块模块的 pageProps 由 CockpitApp 每次渲染新建对象;给注册表一个身份稳定、读取永远最新的视图。 */
function useLiveRecord<T extends Record<string, unknown>>(value: T): T {
  const ref = useLatestRef(value);
  return React.useMemo(() => new Proxy({} as T, {
    get: (_target, key) => (ref.current as Record<PropertyKey, unknown>)[key],
    has: (_target, key) => key in ref.current,
    ownKeys: () => Reflect.ownKeys(ref.current),
    getOwnPropertyDescriptor: (_target, key) => {
      const descriptor = Object.getOwnPropertyDescriptor(ref.current, key);
      return descriptor ? { ...descriptor, configurable: true } : undefined;
    },
  }), [ref]);
}

export function DashboardEditablePage(props: any) {
  const { t: contextT } = useT();
  const {
    dashboardEditing = false,
    kpiScope = "all",
    t: translate,
    apiToken = "",
    onNavigate,
    onOpenProjectsList,
    onOpenMyKol,
    onOpenEvents,
    crossBoardPageProps = EMPTY_OBJECT,
    dashboardStorageScope = "",
    // usePermissions().canViewBoard 同源(CockpitApp 下传)。缺少权限上下文时默认不暴露业务页模块。
    canViewBoard = () => false,
  } = props;
  const t = typeof translate === "function" ? translate : contextT;

  // 渲染期读取的最新快照(数据 + setter + 回调);模块定义本身不依赖它们的身份。
  const live = useLatestRef({
    props,
    t,
    kpiScope,
    setKpiScope: props.setKpiScope ?? (() => undefined),
    setSelectedKpi: props.setSelectedKpi,
    dashboardLoading: props.dashboardLoading ?? false,
    dashboardError: props.dashboardError ?? "",
    sourceHealth: props.sourceHealth ?? null,
    metrics: props.metrics ?? [],
    signals: props.signals ?? [],
    aiInsight: props.aiInsight ?? EMPTY_AI_INSIGHT,
    topMovers: props.topMovers ?? [],
    campaigns: props.campaigns ?? [],
    campaignsMeta: props.campaignsMeta ?? EMPTY_OBJECT,
    calendarDays: props.calendarDays ?? [],
    calendarMeta: props.calendarMeta ?? EMPTY_OBJECT,
    kolFunnel: props.kolFunnel ?? null,
    upcomingEvents: props.upcomingEvents ?? [],
    setSelectedSignal: props.setSelectedSignal,
    setShowAllSignals: props.setShowAllSignals,
    setShowAIConfirm: props.setShowAIConfirm,
    aiRegenerating: props.aiRegenerating ?? false,
    aiRegeneration: props.aiRegeneration ?? { phase: "idle", message: "" },
    onRegenerateAiToday: props.onRegenerateAiToday,
    setSelectedMover: props.setSelectedMover,
    setShowAllMovers: props.setShowAllMovers,
    setSelectedProject: props.setSelectedProject,
    setShowAllProjects: props.setShowAllProjects,
    setSelectedPublish: props.setSelectedPublish,
    setShowFullCalendar: props.setShowFullCalendar,
    setSelectedEvent: props.setSelectedEvent,
    onOpenProjectsList,
    onOpenMyKol,
    onOpenEvents,
    onOpenTriage: props.onOpenTriage,
    onNavigate,
    canViewBoard,
  });
  const livePageProps = useLiveRecord(crossBoardPageProps as Record<string, unknown>);

  const openBoard = React.useCallback((key: string) => {
    const current = live.current;
    if (current.onNavigate) current.onNavigate(key);
    else if (key === "my-kol") current.onOpenMyKol?.();
    else if (key === "projects") current.onOpenProjectsList?.();
    else if (key === "events") current.onOpenEvents?.();
  }, [live]);

  const scrollToDashboardModule = React.useCallback((moduleKey: string) => {
    const target = document.querySelector<HTMLElement>(`[data-dashboard-module="${moduleKey}"]`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.focus({ preventScroll: true });
  }, []);

  // canViewBoard 每次渲染都是新函数(usePermissions 未 memo);按结果签名而非身份触发重建。
  const boardVisibilitySignature = [
    ...BOARD_LINK_NAV_KEYS,
    ...CROSS_BOARD_SOURCES.map((source) => source.board),
  ].map((board) => (canViewBoard(board) ? "1" : "0")).join("");

  const modules = React.useMemo<DashboardModuleDefinition[]>(() => {
    const canView = (board: string) => live.current.canViewBoard(board);
    const visibleBoardModule = (
      board: string,
      definition: DashboardModuleDefinition,
    ): DashboardModuleDefinition[] => canView(board) ? [definition] : [];
    const lazy = (Component: React.LazyExoticComponent<React.ComponentType<any>>, moduleProps: Record<string, unknown>) =>
      renderLazyModule(Component, moduleProps, t("模块加载中…"));
    const boardLink = (navKey: string, card: { label: string; summary: string; Icon: LucideIcon; metric?: () => string | number | null }) =>
      e(DashboardBoardLinkCard, { label: t(card.label), summary: t(card.summary), metric: card.metric ? card.metric() : undefined, Icon: card.Icon, onOpen: () => openBoard(navKey) });

    const sourceModules: DashboardModuleDefinition[] = [
      {
        key: "kpi", label: "增长总览", description: "六指标 × 全部 / KOL / 公司账号", category: "核心模块", defaultSpan: 12, minSpan: 12, defaultHeight: 6, minHeight: 5, maxHeight: 12,
        render: () => {
          const { metrics, kpiScope: scope, setKpiScope, setSelectedKpi, dashboardLoading, dashboardError, sourceHealth } = live.current;
          const sourceStatusLabel = dashboardLoading
            ? t("读取中")
            : dashboardError
              ? t("无信号")
              : sourceHealth?.available
                ? t(sourceHealth.label)
                // sourceHealth 不可用≠数据实时;诚实口径与 EventRadarCoverage「来源状态未知」对齐,禁伪装成「实时」。
                : t("状态未知");
          return e("section", { className: "vkpi-kpi-overview" },
            e("div", { className: "vkpi-kpi-overview__head" },
              e("div", { className: "flex items-center gap-2" }, e("h2", null, t("增长总览")), e("span", { className: "vkpi-kpi-overview__count" }, `${metrics.length || 6} ${t("指标")}`)),
              e("div", { className: "vkpi-kpi-overview__tools" },
                e("div", { className: "vkpi-kpi-overview__scopes" }, Object.entries(KPI_SCOPES).map(([key, scopeMeta]: [string, any]) => e("button", { key, onClick: () => setKpiScope(key), className: scope === key ? "is-active" : "", style: scope === key ? { background: scopeMeta.accent, color: scopeMeta.color } : undefined }, t(scopeMeta.shortLabel)))),
                e("span", {
                  role: "status",
                  "aria-label": t("KPI data status"),
                  title: sourceHealth?.detail || undefined,
                  className: `vkpi-kpi-overview__status ${dashboardError ? "is-error" : sourceHealth?.degraded ? "is-degraded" : ""}`,
                }, sourceStatusLabel),
              ),
            ),
            e("div", { className: "vkpi-kpi-overview__grid grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6" }, metrics.map((metric: any, index: number) => e(MetricCard, { key: metric.id, item: metric, i: index, scope, onClick: () => setSelectedKpi(metric.id) }))),
          );
        },
      },
      { key: "command-center", label: "命令中心地图", description: "真实 Leaflet 地图、层级与 pin 下钻", category: "核心模块", defaultSpan: 8, minSpan: 7, defaultHeight: 15, minHeight: 10, maxHeight: 24, render: () => e(DashboardCommandCenter, { ...live.current.props, t, viewModes: live.current.props.viewModes || VIEW_MODES }) },
      { key: "north-star", label: "增长健康度", description: "真实 GTM North Star 与流动环", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 10, minHeight: 8, maxHeight: 18, render: () => apiToken ? e(NorthStarGauges, { apiToken, variant: "dashboard" }) : null },
      { key: "actions", label: "今日该做什么", description: "Action Inbox 审批与执行建议", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 11, minHeight: 8, maxHeight: 20, render: () => apiToken ? e(ActionInboxPanel, { apiToken, heading: "今日该做什么", limit: 3 }) : null },
      { key: "signals", label: "市场信号", description: "真实竞品雷达与证据下钻", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 11, minHeight: 8, maxHeight: 20, render: () => live.current.dashboardError
        ? e("article", { className: "vkpi-dashboard-error-card" }, e(AlertTriangle, { size: 16 }), e("strong", null, t("信号源异常")), e("span", null, t("当前接口暂不可用")))
        : e(SignalsAlertsCard, { alerts: live.current.signals, onAlertClick: live.current.setSelectedSignal, onViewAll: () => live.current.setShowAllSignals(true) }) },
      { key: "v6-fit", label: "V6 Fit Top", description: "真实 KOL Pool 高拟合候选", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 10, minHeight: 7, maxHeight: 18, render: () => e(TopMoversCard, { movers: live.current.topMovers, onMoverClick: live.current.setSelectedMover, onViewAll: () => live.current.setShowAllMovers(true) }) },
      { key: "trend", label: "曝光与互动趋势", description: "真实 KPI 连续窗口趋势", category: "核心模块", defaultSpan: 8, minSpan: 6, defaultHeight: 9, minHeight: 7, maxHeight: 18, render: () => e(DashboardTrendCard, { metrics: live.current.metrics, scope: live.current.kpiScope }) },
      { key: "ai-today", label: "AI Today", description: "真实每日决策、证据与拍摄建议", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 15, minHeight: 10, maxHeight: 26, render: () => lazy(AIIntelligenceCard, { insight: live.current.aiInsight, onApprove: () => live.current.setShowAIConfirm(true), onLater: null, onRegenerate: live.current.onRegenerateAiToday, regenerating: live.current.aiRegenerating, regenerationState: live.current.aiRegeneration }) },
      { key: "task-queue", label: "智能任务队列", description: "真实抓取 / 分析 / 落库 / 排队与成本", category: "核心模块", defaultSpan: 4, defaultHeight: 9, minHeight: 7, render: () => e(DashboardTaskQueueCard, { apiToken }) },
      { key: "memo", label: "备忘录", description: "账户偏好中保存的计划与待办", category: "核心模块", defaultSpan: 4, minSpan: 4, defaultHeight: 9, minHeight: 7, maxHeight: 18, allowMultiple: true, render: () => e(DashboardMemoCard, { apiToken }) },
      { key: "trend-pulse", label: "市场热词", description: "近期行业帖热词与真实稀疏态", category: "核心模块", defaultSpan: 8, render: () => e(TrendPulseBar, { apiToken }) },

      // 2026-07-16 排版修复:此前无 defaultHeight → 吃 9 格默认高,而横条内容只有一行,
      // 板内 min-height:100% 把 bg-good-soft 绿底撑成大片空绿地(chips 漂中间)。收紧到 3-6 格;
      // min/max 同时会把用户已存布局里的旧高度钳回合理区间(normalizeModuleHeight 读定义钳制)。
      { key: "today-focus", label: "今日焦点", description: "Action Inbox 与晨报摘要", category: "实时模块", defaultSpan: 12, defaultHeight: 4, minHeight: 3, maxHeight: 6, render: () => apiToken ? e(TodayFocusStrip, { apiToken, onJumpToInbox: () => scrollToDashboardModule("actions"), onJumpToBrief: () => scrollToDashboardModule("morning-brief") }) : null },
      { key: "morning-brief", label: "夜班晨报", description: "昨夜自动任务结果", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(MorningBriefCard, { apiToken }) : null },
      { key: "gifted-funnel", label: "送样履约漏斗", description: "签收、开窗、发布与超期", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(GiftedFunnelPanel, { apiToken }) : null },
      { key: "activity-feed", label: "思考流", description: "任务完成、告警与会话推进", category: "实时模块", defaultSpan: 4, render: () => apiToken ? e(ActivityFeed, { apiToken }) : null },
      { key: "semantic-recall", label: "语义找人", description: "三段式召回与降级状态", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(SemanticRecallCard, { apiToken }) : null },
      { key: "comment-opportunities", label: "评论区机会", description: "值得官号参与的真实内容", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(CommentOpportunitiesPanel, { apiToken }) : null },
      { key: "brand-pulse", label: "品牌脉搏", description: "90 天品牌声量和 SoV", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(BrandPulsePanel, { apiToken }) : null },
      ...(SHOW_EXPERIMENTAL ? [{ key: "ops-health", label: "系统健康", description: "黄金链路健康检查", category: "实时模块" as const, defaultSpan: 4, render: () => apiToken ? e(OpsHealthCard, { apiToken, onOpenTriage: live.current.onOpenTriage }) : null }] : []),
      { key: "worker-devices", label: "本地算力节点", description: "设备在线与租约状态", category: "实时模块", defaultSpan: 4, render: () => apiToken ? lazy(WorkerDevicesPanel, { apiToken }) : null },
      { key: "kol-funnel", label: "KOL 漏斗", description: "收藏、认领、项目与发布", category: "实时模块", defaultSpan: 4, render: () => e(KolFunnelCard, { funnel: live.current.kolFunnel, onOpenMyKol: live.current.onOpenMyKol }) },
      { key: "active-campaigns", label: "Active Campaigns", description: "真实在推项目与履约进度", category: "实时模块", defaultSpan: 4, render: () => e(ActiveCampaignsCard, { campaigns: live.current.campaigns, campaignsMeta: live.current.campaignsMeta, onCampaignClick: live.current.setSelectedProject, onViewAll: () => live.current.onOpenProjectsList ? live.current.onOpenProjectsList() : live.current.setShowAllProjects(true) }) },
      { key: "content-calendar", label: "内容日历", description: "近 7 天真实发布信号", category: "实时模块", defaultSpan: 8, render: () => e(ContentCalendarCard, { days: live.current.calendarDays, latestDate: live.current.calendarMeta?.latestDate, onItemClick: live.current.setSelectedPublish, onViewAll: () => live.current.setShowFullCalendar(true) }) },
      { key: "upcoming-events", label: "Upcoming Events", description: "真实近期活动", category: "实时模块", defaultSpan: 4, render: () => e(UpcomingEventsCard, { events: live.current.upcomingEvents, dragConstraintsRef: live.current.props.globeContainerRef, onEventClick: live.current.setSelectedEvent, onViewAll: live.current.onOpenEvents }) },

      ...visibleBoardModule("my-kol", { key: "board-my-kol", label: "MY KOL", description: "合作达人、跟进与推进", category: "业务板块", defaultSpan: 4, render: () => boardLink("my-kol", { label: "MY KOL", summary: "合作与跟进工作台", Icon: CircleUserRound }) }),
      ...visibleBoardModule("kol-pool", { key: "board-kol-pool", label: "KOL Pool", description: "候选发现、评分与档案", category: "业务板块", defaultSpan: 4, render: () => boardLink("kol-pool", { label: "KOL Pool", summary: "候选与高拟合发现", Icon: Database, metric: () => {
        const roster = live.current.metrics.find((metric: any) => metric?.id === "kol-count");
        return formatRoster(roster?.data?.[live.current.kpiScope]?.value ?? roster?.data?.all?.value);
      } }) }),
      ...visibleBoardModule("kolProfile", { key: "board-kol-profile", label: "KOL 档案", description: "单个达人八层档案", category: "业务板块", defaultSpan: 4, render: () => boardLink("kolProfile", { label: "KOL 档案", summary: "身份、内容、受众与合作", Icon: IdCard }) }),
      ...visibleBoardModule("projects", { key: "board-projects", label: "Projects", description: "项目履约、成本与证据", category: "业务板块", defaultSpan: 4, render: () => boardLink("projects", { label: "Projects", summary: "项目履约与复盘", Icon: BriefcaseBusiness, metric: () => live.current.campaigns.length || null }) }),
      ...visibleBoardModule("events", { key: "board-events", label: "Events", description: "活动、物料与成员分工", category: "业务板块", defaultSpan: 4, render: () => boardLink("events", { label: "Events", summary: "近期真实活动", Icon: CalendarDays, metric: () => live.current.upcomingEvents.length || null }) }),
      ...visibleBoardModule("shopify", { key: "board-shopify", label: "Shopify", description: "联盟链接与订单归因", category: "业务板块", defaultSpan: 4, render: () => boardLink("shopify", { label: "Shopify", summary: "订单归因待接入", Icon: ShoppingBag }) }),
      ...visibleBoardModule("dealers", { key: "board-dealers", label: "Dealers", description: "经销商地理分布", category: "业务板块", defaultSpan: 4, render: () => boardLink("dealers", { label: "Dealers", summary: "经销商地图", Icon: MapPin }) }),
      ...visibleBoardModule("intelligent", { key: "board-intelligent", label: "Intelligent 问答", description: "内部数据问答", category: "业务板块", defaultSpan: 4, render: () => boardLink("intelligent", { label: "Intelligent 问答", summary: "问市场、KOL 与项目", Icon: MessageCircleMore }) }),
      ...visibleBoardModule("marketVoice", { key: "board-market-voice", label: "市场之声", description: "用户反馈与产品信号", category: "业务板块", defaultSpan: 4, render: () => boardLink("marketVoice", { label: "市场之声", summary: "评论与需求聚类", Icon: RadioTower }) }),
      ...visibleBoardModule("sku360", { key: "board-sku360", label: "SKU 360°", description: "产品、内容与达人反查", category: "业务板块", defaultSpan: 4, render: () => boardLink("sku360", { label: "SKU 360°", summary: "产品视角完整档案", Icon: Boxes }) }),
      ...visibleBoardModule("creativeLibrary", { key: "board-creative", label: "创意资产库", description: "段级内容资产检索", category: "业务板块", defaultSpan: 4, render: () => boardLink("creativeLibrary", { label: "创意资产库", summary: "开头、画面与产品露出", Icon: LibraryBig }) }),
      ...visibleBoardModule("replyQueue", { key: "board-reply", label: "回复队列", description: "高意图评论与人工回复", category: "业务板块", defaultSpan: 4, render: () => boardLink("replyQueue", { label: "回复队列", summary: "AI 草稿，人工放行", Icon: MessagesSquare }) }),
      ...visibleBoardModule("launchpad", { key: "board-launchpad", label: "发射台", description: "新品一键六输出", category: "业务板块", defaultSpan: 4, render: () => boardLink("launchpad", { label: "发射台", summary: "新品上市一键全案", Icon: Rocket }) }),
      ...visibleBoardModule("autonomy", { key: "board-autonomy", label: "自治驾照", description: "自动化等级与审批红线", category: "业务板块", defaultSpan: 4, render: () => boardLink("autonomy", { label: "自治驾照", summary: "挣来的自治", Icon: ShieldCheck }) }),
      ...visibleBoardModule("strategyBoard", { key: "board-strategy", label: "战略台", description: "行业对照与策略模拟", category: "业务板块", defaultSpan: 4, render: () => boardLink("strategyBoard", { label: "战略台", summary: "赛道与预算模拟", Icon: Target }) }),
      ...visibleBoardModule("gtmCommand", { key: "board-gtm", label: "GTM Command", description: "产品上市增长指挥图", category: "业务板块", defaultSpan: 4, render: () => boardLink("gtmCommand", { label: "GTM Command", summary: "P2G 总脑出口", Icon: Compass }) }),

      // 跨板块模块(task #76):子板块注册表模块拉进 Dashboard 直接操作(全部 palette
      // 备选,默认布局不动)。真身 lazy 分板块加载;canViewBoard 过滤 palette 可见性。
      // pageProps 走 live 视图:CockpitApp 每帧新建的对象不再让这批定义失效。
      ...buildCrossBoardModules({ apiToken, canViewBoard: canView, onOpenBoard: openBoard, pageProps: livePageProps as Record<string, Record<string, unknown>> }),
    ];
    return sourceModules.map((definition) => ({
      ...definition,
      label: t(definition.label),
      description: t(definition.description),
      sourceLabel: definition.sourceLabel ? t(definition.sourceLabel) : definition.sourceLabel,
    }));
    // boardVisibilitySignature 代表 canViewBoard 的结果集合(函数身份每帧变化,不能直接作依赖)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, apiToken, boardVisibilitySignature, openBoard, scrollToDashboardModule, livePageProps, live]);

  return e("div", { className: "vkpi-dashboard-canvas p-4 md:px-[22px] md:py-[15px]" },
      e(EditableDashboardBoard, {
        key: `dashboard-layout-${dashboardStorageScope || "unscoped"}`,
        modules,
        defaultLayout: DEFAULT_LAYOUT,
        requiredDefaultModuleKeys: REQUIRED_V5_DEFAULT_MODULE_KEYS,
        editing: dashboardEditing,
        apiToken,
        localStorageScope: dashboardStorageScope,
      }),
  );
}
