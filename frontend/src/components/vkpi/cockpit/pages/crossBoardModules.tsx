import React from "react";
import type { DashboardModuleDefinition } from "../components/EditableDashboardBoard";

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

interface XbCardProps {
  apiToken: string;
  onOpenBoard: () => void;
  onNavigate: (navKey: string) => void;
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
  Component: React.LazyExoticComponent<React.ComponentType<XbCardProps>>;
}

// 首批 9 件(2026-07-12 逐件核实「不依赖页级 state」后收编;尺寸镜像源板块注册表)
export const CROSS_BOARD_ENTRIES: CrossBoardEntry[] = [
  {
    key: "xb-voice-alerts", board: "marketVoice", boardLabel: "市场之声",
    label: "声量告警", description: "类别 × 8h 负面阈值触发 · 正常态全绿",
    defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 20,
    Component: React.lazy(() => import("./crossBoardModules.voice").then((m) => ({ default: m.VoiceAlertsXbCard }))),
  },
  {
    key: "xb-voice-senti", board: "marketVoice", boardLabel: "市场之声",
    label: "情绪趋势", description: "正/负占比双线 · 空期断线 · 日/周自适应",
    defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 6, maxHeight: 20,
    Component: React.lazy(() => import("./crossBoardModules.voice").then((m) => ({ default: m.VoiceSentiXbCard }))),
  },
  {
    key: "xb-mykol-funnel", board: "my-kol", boardLabel: "MY KOL",
    label: "合作漏斗", description: "8 段真阶段条形 · 点段跳源板块",
    defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16,
    Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolFunnelXbCard }))),
  },
  {
    key: "xb-mykol-activity", board: "my-kol", boardLabel: "MY KOL",
    label: "分析动态", description: "进行中的账号分析/深析/评论采集 · 点行直达 KOL Pool",
    defaultSpan: 4, minSpan: 3, defaultHeight: 13, minHeight: 4, maxHeight: 20,
    Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolActivityXbCard }))),
  },
  {
    key: "xb-mykol-content-wall", board: "my-kol", boardLabel: "MY KOL",
    label: "内容墙", description: "收藏集最近采集视频网格 · KOL/仅V/排序筛选 · 点卡直跳原帖",
    defaultSpan: 8, minSpan: 4, defaultHeight: 13, minHeight: 5, maxHeight: 30,
    Component: React.lazy(() => import("./crossBoardModules.mykol").then((m) => ({ default: m.MyKolContentWallXbCard }))),
  },
  {
    key: "xb-pool-discovery-funnel", board: "kol-pool", boardLabel: "KOL Pool",
    label: "发现转化 · 近30天", description: "发现 → 自动入库 → 已深析 → 已收藏 四段(同窗计数)",
    defaultSpan: 4, minSpan: 3, defaultHeight: 11, minHeight: 4, maxHeight: 16,
    Component: React.lazy(() => import("./crossBoardModules.pool").then((m) => ({ default: m.PoolDiscoveryFunnelXbCard }))),
  },
  {
    key: "xb-projects-due", board: "projects", boardLabel: "Projects",
    label: "履约待办", description: "已签收满 7 天未推进的项目 · 点行直开项目详情",
    defaultSpan: 8, minSpan: 4, defaultHeight: 9, minHeight: 5, maxHeight: 24,
    Component: React.lazy(() => import("./crossBoardModules.projects").then((m) => ({ default: m.ProjectsDueXbCard }))),
  },
  {
    key: "xb-reply-intent", board: "replyQueue", boardLabel: "回复队列",
    label: "意向构成", description: "价格/兼容/问询/手动入队 环图 · 点分段跳源板块",
    defaultSpan: 4, minSpan: 3, defaultHeight: 6, minHeight: 5, maxHeight: 16,
    Component: React.lazy(() => import("./crossBoardModules.reply").then((m) => ({ default: m.ReplyIntentXbCard }))),
  },
  {
    key: "xb-reply-funnel", board: "replyQueue", boardLabel: "回复队列",
    label: "处理进度", description: "待起草 → 待回复 → 已回复/已忽略 状态漏斗",
    defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16,
    Component: React.lazy(() => import("./crossBoardModules.reply").then((m) => ({ default: m.ReplyFunnelXbCard }))),
  },
];

export interface CrossBoardModulesOptions {
  apiToken: string;
  /** usePermissions().canViewBoard 同源(CockpitApp 下传);看不见的板块模块不进 palette */
  canViewBoard: (navKey: string) => boolean;
  /** 跳板块(CockpitApp setActiveNav 管道) */
  onOpenBoard: (navKey: string) => void;
}

export function buildCrossBoardModules(options: CrossBoardModulesOptions): DashboardModuleDefinition[] {
  return CROSS_BOARD_ENTRIES.filter((entry) => options.canViewBoard(entry.board)).map((entry) => ({
    key: entry.key,
    label: entry.label,
    description: `${entry.boardLabel} · ${entry.description}`,
    category: CROSS_BOARD_CATEGORY,
    defaultSpan: entry.defaultSpan,
    minSpan: entry.minSpan,
    defaultHeight: entry.defaultHeight,
    minHeight: entry.minHeight,
    maxHeight: entry.maxHeight,
    render: () =>
      React.createElement(
        React.Suspense,
        { fallback: React.createElement("div", { className: "vkpi-dashboard-lazy-module" }, "模块加载中…") },
        React.createElement(entry.Component, {
          apiToken: options.apiToken,
          onOpenBoard: () => options.onOpenBoard(entry.board),
          onNavigate: options.onOpenBoard,
        }),
      ),
  }));
}
