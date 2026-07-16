import React from "react";
import type { DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import { LazyErrorBoundary } from "../components/LazyErrorBoundary";
import { FULL_BOARD_MODULE_CATALOG } from "./crossBoardModules.catalog.generated";

// Dashboard 跨板块拉卡(task #76)· 注册表(唯一被 DashboardEditablePage 静态引用的桥文件)。
//   让 Dashboard palette 可拉入子板块注册表模块:用户在 Dashboard 直接操作,不用切页。
//   本文件必须保持轻(只有 React + 类型):真身卡全部 React.lazy 按源板块分文件
//   (crossBoardModules.<board>.tsx),不拖重 Dashboard 首屏 chunk。
//   收编红线(手册 §4.4 前提):
//     · 模块自带取数 —— 每张卡 fetch 自己调(apiToken 透传),零依赖源板块页级 state;
//     · key 带板块前缀(xb-*)—— 进 Dashboard 账户级布局键 dashboard_layout_v1,
//       与 Dashboard 既有模块 key 零冲突;默认布局不动,全部 palette 备选;
//     · 权限 —— canViewBoard(usePermissions 同源,CockpitApp 下传)过滤:看不见的
//       板块,其模块不出现在 palette;已存布局里的残留项经 moduleMap 过滤自动不渲染;
//     · 卡头带「来源板块」小徽,点击跳源板块(onNavigate 管道);SrcChip 沿用源
//       MODULE_SOURCES 口径;诚实空态 / reduced-motion 全沿源模块。

export const CROSS_BOARD_CATEGORY = "跨板块模块" as const;
export type CrossBoardAvailability = "ready" | "context";

interface XbCardProps {
  apiToken: string;
  onOpenBoard: () => void;
  onNavigate: (navKey: string) => void;
  board?: string;
  boardLabel?: string;
  sourceModuleKey?: string;
  pageProps?: Record<string, Record<string, unknown>>;
}

export interface CrossBoardEntry {
  /** 模块 key(xb-<board>-<module>,进 dashboard_layout_v1) */
  key: string;
  /** 源板块 navKey(CockpitApp NAV_ITEMS / canViewBoard 同一口径) */
  board: string;
  /** 源板块名(palette 描述前缀 + 卡头来源徽,与侧栏同文) */
  boardLabel: string;
  label: string;
  description: string;
  defaultSpan: number;
  minSpan: number;
  defaultHeight: number;
  minHeight: number;
  maxHeight: number;
  /** ready=挂载即可自取数；context=需要源页已选择实体，缺失时显示诚实待接态。 */
  availability: CrossBoardAvailability;
  /** 源页面注册表 key；存在时由通用嵌入宿主直接渲染源模块原件。 */
  sourceModuleKey?: string;
  Component: React.LazyExoticComponent<React.ComponentType<XbCardProps>>;
}

type CrossBoardSourceEntry = Omit<CrossBoardEntry, "board" | "boardLabel">;

export interface CrossBoardSourceGroup {
  board: string;
  boardLabel: string;
  entries: CrossBoardSourceEntry[];
}

// 按侧栏业务页顺序维护来源组。Dashboard palette 使用同一顺序扁平展示，
// description 始终以前缀标明来源页；来源组本身可供测试和后续 palette 分节直接消费。
const CURATED_CROSS_BOARD_SOURCES: CrossBoardSourceGroup[] = [
  {
    board: "my-kol",
    boardLabel: "MY KOL",
    entries: [
      {
        key: "xb-mykol-funnel", label: "合作漏斗", description: "8 段真阶段条形 · 点段跳源板块",
        defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolFunnelXbCard }))),
      },
      {
        key: "xb-mykol-activity", label: "分析动态", description: "进行中的账号分析/深析/评论采集 · 点行直达 KOL Pool",
        defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolActivityXbCard }))),
      },
      {
        key: "xb-mykol-content-wall", label: "内容墙", description: "收藏集最近采集视频网格 · KOL/仅V/排序筛选 · 点卡直跳原帖",
        defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 5, maxHeight: 30,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolContentWallXbCard }))),
      },
    ],
  },
  {
    board: "kol-pool",
    boardLabel: "KOL Pool",
    entries: [
      {
        key: "xb-pool-smart-search", label: "找达人", description: "贴链接或描述需求直接查找 · 搜索历史与已移除记录同源可查",
        defaultSpan: 12, minSpan: 6, defaultHeight: 13, minHeight: 6, maxHeight: 34,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.pool").then((m) => ({ default: m.PoolSmartSearchXbCard }))),
      },
      {
        key: "xb-pool-search-history", label: "搜索历史", description: "当前账号最近与已移除搜索会话 · 可打开、移除和恢复",
        defaultSpan: 8, minSpan: 4, defaultHeight: 10, minHeight: 5, maxHeight: 26,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.pool").then((m) => ({ default: m.PoolSearchHistoryXbCard }))),
      },
      {
        key: "xb-pool-discovery-funnel", label: "发现转化 · 近30天", description: "发现 → 自动入库 → 已深析 → 已收藏 四段(同窗计数)",
        defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.pool").then((m) => ({ default: m.PoolDiscoveryFunnelXbCard }))),
      },
    ],
  },
  {
    board: "kolProfile",
    boardLabel: "KOL 档案",
    entries: [
      {
        key: "xb-profile-signature", label: "招牌内容", description: "读取最近打开的达人档案 · 未选择时诚实待接",
        defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 24,
        availability: "context",
        Component: React.lazy(() => import("./crossBoardModules.profile").then((m) => ({ default: m.ProfileSignatureXbCard }))),
      },
    ],
  },
  {
    board: "projects",
    boardLabel: "Projects",
    entries: [
      {
        key: "xb-projects-due", label: "履约待办", description: "已签收满 7 天未推进的项目 · 点行直开项目详情",
        defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 24,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.projects").then((m) => ({ default: m.ProjectsDueXbCard }))),
      },
    ],
  },
  {
    board: "events",
    boardLabel: "Events",
    entries: [
      {
        key: "xb-events-radar", label: "活动雷达", description: "Dealer / 线下活动与全球大展会 · 人工批准后才转内部 Event",
        defaultSpan: 12, minSpan: 6, defaultHeight: 22, minHeight: 10, maxHeight: 44,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.events").then((m) => ({ default: m.EventsRadarXbCard }))),
      },
      {
        key: "xb-events-upcoming", label: "即将开幕", description: "未来窗口内真实活动 · 点行进入详情",
        defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.events").then((m) => ({ default: m.EventsUpcomingXbCard }))),
      },
    ],
  },
  {
    board: "shopify",
    boardLabel: "Shopify",
    entries: [
      {
        key: "xb-shopify-gmv", label: "GMV 对账", description: "订单台账优先 · 无订单回落归因净额 · 两账空则诚实空",
        defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 5, maxHeight: 18,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.shopify").then((m) => ({ default: m.ShopifyGmvXbCard }))),
      },
    ],
  },
  {
    board: "dealers",
    boardLabel: "Dealers",
    entries: [
      {
        key: "xb-dealers-regions", label: "地区分布", description: "经销商按州真实聚合 Top 10 · 库空不画分布",
        defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 5, maxHeight: 22,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.dealers").then((m) => ({ default: m.DealersRegionsXbCard }))),
      },
    ],
  },
  { board: "triage", boardLabel: "运维 Triage", entries: [] },
  { board: "dataQuery", boardLabel: "问数", entries: [] },
  { board: "marketTrends", boardLabel: "市场趋势", entries: [] },
  { board: "skillStudio", boardLabel: "Skill Studio", entries: [] },
  {
    board: "intelligent",
    boardLabel: "Intelligent 问答",
    entries: [
      {
        key: "xb-intelligent-stats", label: "问答调用趋势", description: "服务端综合问答 14 天留痕 · 空统计不编调用量",
        defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 5, maxHeight: 18,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.intelligent").then((m) => ({ default: m.IntelligentStatsXbCard }))),
      },
    ],
  },
  {
    board: "marketVoice",
    boardLabel: "市场之声",
    entries: [
      {
        key: "xb-voice-alerts", label: "声量告警", description: "类别 × 8h 负面阈值触发 · 正常态全绿",
        defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.voice").then((m) => ({ default: m.VoiceAlertsXbCard }))),
      },
      {
        key: "xb-voice-senti", label: "情绪趋势", description: "正/负占比双线 · 空期断线 · 日/周自适应",
        defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 6, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.voice").then((m) => ({ default: m.VoiceSentiXbCard }))),
      },
    ],
  },
  {
    board: "sku360",
    boardLabel: "SKU 360°",
    entries: [
      {
        key: "xb-sku360-catalog", label: "产品档案", description: "优先当前 SKU · 无上下文时只读目录首项",
        defaultSpan: 8, minSpan: 4, defaultHeight: 14, minHeight: 7, maxHeight: 28,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.sku").then((m) => ({ default: m.SkuCatalogXbCard }))),
      },
    ],
  },
  {
    board: "creativeLibrary",
    boardLabel: "创意资产库",
    entries: [
      {
        key: "xb-creative-index", label: "索引健康", description: "深析视频、段级索引、覆盖 KOL 与缩略图可用率",
        defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 5, maxHeight: 18,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.creative").then((m) => ({ default: m.CreativeIndexXbCard }))),
      },
    ],
  },
  {
    board: "replyQueue",
    boardLabel: "回复队列",
    entries: [
      {
        key: "xb-reply-intent", label: "意向构成", description: "价格/兼容/问询/手动入队 环图 · 点分段跳源板块",
        defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 5, maxHeight: 16,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.reply").then((m) => ({ default: m.ReplyIntentXbCard }))),
      },
      {
        key: "xb-reply-funnel", label: "处理进度", description: "待起草 → 待回复 → 已回复/已忽略 状态漏斗",
        defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.reply").then((m) => ({ default: m.ReplyFunnelXbCard }))),
      },
    ],
  },
  {
    board: "launchpad",
    boardLabel: "发射台",
    entries: [
      {
        key: "xb-launchpad-launches", label: "发布计划", description: "真实产品发布计划、状态与发布窗口",
        defaultSpan: 8, minSpan: 4, defaultHeight: 8, minHeight: 5, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.launchpad").then((m) => ({ default: m.LaunchpadPlansXbCard }))),
      },
    ],
  },
  {
    board: "autonomy",
    boardLabel: "自治驾照",
    entries: [
      {
        key: "xb-autonomy-scorecard", label: "周度记分卡", description: "8 周真实裁决命中率与待对答案积压",
        defaultSpan: 8, minSpan: 4, defaultHeight: 12, minHeight: 6, maxHeight: 26,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.autonomy").then((m) => ({ default: m.AutonomyScorecardXbCard }))),
      },
    ],
  },
  {
    board: "strategyBoard",
    boardLabel: "战略台",
    entries: [
      {
        key: "xb-strategy-sov", label: "声量份额排名", description: "近 90 天品牌视频声量与覆盖 KOL 排名",
        defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 20,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.strategy").then((m) => ({ default: m.StrategySovXbCard }))),
      },
    ],
  },
  {
    board: "gtmCommand",
    boardLabel: "GTM Command",
    entries: [
      {
        key: "xb-gtm-signals", label: "本周信号", description: "Market Brain 纯读聚合信号、样本与置信",
        defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 22,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.gtm").then((m) => ({ default: m.GtmSignalsXbCard }))),
      },
      {
        key: "xb-gtm-ai-readiness", label: "AI 证据就绪度", description: "能力分与真实证据分分离 · outcome / feedback / actual 三腿硬门槛",
        defaultSpan: 4, minSpan: 3, defaultHeight: 10, minHeight: 6, maxHeight: 22,
        availability: "ready",
        Component: React.lazy(() => import("./crossBoardModules.gtm").then((m) => ({ default: m.GtmAiReadinessXbCard }))),
      },
    ],
  },
];

const FullBoardModuleCard = React.lazy(() => import("./crossBoardModules.full").then((module) => ({
  default: module.FullBoardModuleXbCard,
})));

// 已经有轻量、自取数原件的模块继续复用；其余页面注册表模块统一走通用嵌入宿主。
// 这样 Dashboard 不再只展示“代表卡”，同时避免同一源模块在 palette 重复两次。
const CURATED_SOURCE_REPLACEMENTS = new Set([
  "my-kol:funnel", "my-kol:activity", "my-kol:contentWall",
  "kol-pool:smart", "kol-pool:funnel",
  "kolProfile:signature",
  "projects:dueP",
  "events:radar", "events:upcoming",
  "dealers:regionD",
  "marketVoice:alerts", "marketVoice:senti",
  "sku360:catalog",
  "replyQueue:intent", "replyQueue:funnel",
  "launchpad:launches",
  "autonomy:scorecard",
  "strategyBoard:rank",
  "gtmCommand:signals",
]);

function fullModuleKey(board: string, moduleKey: string) {
  const safe = (value: string) => value.replace(/([a-z0-9])([A-Z])/g, "$1-$2").replace(/[^a-zA-Z0-9]+/g, "-").toLowerCase();
  return `xb-full-${safe(board)}-${safe(moduleKey)}`;
}

export const CROSS_BOARD_SOURCES: CrossBoardSourceGroup[] = CURATED_CROSS_BOARD_SOURCES.map((source) => {
  const catalogSource = FULL_BOARD_MODULE_CATALOG.find((candidate) => candidate.board === source.board);
  return {
    ...source,
    entries: [
      ...source.entries,
      ...(catalogSource?.modules || [])
      .filter((meta) => !CURATED_SOURCE_REPLACEMENTS.has(`${source.board}:${meta.moduleKey}`))
      .map((meta): CrossBoardSourceEntry => ({
        key: fullModuleKey(source.board, meta.moduleKey),
        label: meta.label,
        description: `源页面原模块 · 与 ${source.boardLabel} 同数据、同交互和同空状态`,
        defaultSpan: meta.defaultSpan,
        minSpan: meta.minSpan,
        defaultHeight: meta.defaultHeight,
        minHeight: meta.minHeight,
        maxHeight: meta.maxHeight,
        availability: source.board === "kolProfile" ? "context" : "ready",
        sourceModuleKey: meta.moduleKey,
        Component: FullBoardModuleCard,
      })),
    ],
  };
});

// 向后兼容既有扁平 registry；key 稳定进入 dashboard_layout_v1。
export const CROSS_BOARD_ENTRIES: CrossBoardEntry[] = CROSS_BOARD_SOURCES.flatMap((source) =>
  source.entries.map((entry) => ({ ...entry, board: source.board, boardLabel: source.boardLabel })),
);

export interface CrossBoardModulesOptions {
  apiToken: string;
  /** usePermissions().canViewBoard 同源(CockpitApp 下传);看不见的板块模块不进 palette */
  canViewBoard: (navKey: string) => boolean;
  /** 跳板块(CockpitApp setActiveNav 管道) */
  onOpenBoard: (navKey: string) => void;
  /** 仅源页依赖 CockpitApp 数据的板块需要；其余页面模块自取数。 */
  pageProps?: Record<string, Record<string, unknown>>;
}

export interface CrossBoardDashboardModule extends DashboardModuleDefinition {
  sourceBoard: string;
  sourceLabel: string;
  availability: CrossBoardAvailability;
}

export interface CrossBoardModuleGroup {
  board: string;
  boardLabel: string;
  modules: CrossBoardDashboardModule[];
}

function buildCrossBoardModule(entry: CrossBoardEntry, options: CrossBoardModulesOptions): CrossBoardDashboardModule {
  return {
    key: entry.key,
    label: entry.label,
    description: `${entry.boardLabel} · ${entry.availability === "context" ? "待选择上下文 · " : ""}${entry.description}`,
    category: CROSS_BOARD_CATEGORY,
    sourceBoard: entry.board,
    sourceLabel: entry.boardLabel,
    availability: entry.availability,
    defaultSpan: entry.defaultSpan,
    minSpan: entry.minSpan,
    defaultHeight: entry.defaultHeight,
    minHeight: entry.minHeight,
    maxHeight: entry.maxHeight,
    render: () =>
      React.createElement(
        LazyErrorBoundary,
        { name: `${entry.boardLabel} · ${entry.label}` },
        React.createElement(
          React.Suspense,
          { fallback: React.createElement("div", { className: "vkpi-dashboard-lazy-module" }, "模块加载中…") },
          React.createElement(entry.Component, {
            apiToken: options.apiToken,
            onOpenBoard: () => options.onOpenBoard(entry.board),
            onNavigate: options.onOpenBoard,
            board: entry.board,
            boardLabel: entry.boardLabel,
            sourceModuleKey: entry.sourceModuleKey,
            pageProps: options.pageProps,
          }),
        ),
      ),
  };
}

export function buildCrossBoardModuleGroups(options: CrossBoardModulesOptions): CrossBoardModuleGroup[] {
  return CROSS_BOARD_SOURCES.flatMap((source) => {
    if (!options.canViewBoard(source.board)) return [];
    return [{
      board: source.board,
      boardLabel: source.boardLabel,
      modules: source.entries.map((sourceEntry) => buildCrossBoardModule({
        ...sourceEntry,
        board: source.board,
        boardLabel: source.boardLabel,
      }, options)),
    }];
  });
}

export function buildCrossBoardModules(options: CrossBoardModulesOptions): DashboardModuleDefinition[] {
  return buildCrossBoardModuleGroups(options).flatMap((group) => group.modules);
}
