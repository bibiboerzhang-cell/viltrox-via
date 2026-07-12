// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "../../../app/providers/ThemeProvider";
// 车道B LazyMotion 瘦身:全 cockpit 树只用 m.*(motion.* 已全量替换),features 在此顶层
// 注入一次。选 domMax 而非 domAnimation:FloatingCard 的 drag 与 ActivityFeed 的 layout
// 动画都要 domMax 才生效(domAnimation 会静默砍掉拖拽 = 功能回退,不只是动效)。
import { AnimatePresence, LazyMotion, domMax, m } from "framer-motion";
import { Bell, ChevronDown, DollarSign, FileText, Globe2, HelpCircle, List, Loader2, Menu, MessageCircle, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, TrendingUp, User, X } from "lucide-react";
import "./styles/mockup.css";
import "./styles/cockpit-reference.css";
import "../styles/vkpi-settings-dark.css";
import { KolPoolBoardPage as KOLPoolPage } from "./pages/KolPoolBoardPage";
// 回滚垫:改回 import { KOLPoolPage } from "./KOLPoolPage" 即回旧 KOL Pool
import { ShopifyConnectPage } from "./ShopifyConnectPage";
import { ShopifyHubPage } from "../pages/ShopifyHubPage";
import { DealerMapPage } from "../pages/DealerMapPage";
import { DashboardReplicaPage } from "./DashboardReplicaPage";
import { CockpitSidebar } from "./CockpitSidebar";
import { CockpitMobileNav } from "./CockpitMobileNav";
import { CockpitTopbar } from "./CockpitTopbar";
import { usePermissions } from "../../../hooks/usePermissions";
import { useBrowserAssist, isBrowserAssistEnabled } from "../../../lib/browserAssist/enable";
import { AIIntelligenceCard } from "./components/AIIntelligenceCard";
import { ActiveCampaignsCard } from "./components/ActiveCampaignsCard";
import { Avatar } from "./components/Avatar";
import { Breadcrumb } from "./components/Breadcrumb";
import { ContentCalendarCard } from "./components/ContentCalendarCard";
import { FloatingCard } from "./components/FloatingCard";
import { HierarchyDropdown } from "./components/HierarchyDropdown";
import { LazyErrorBoundary } from "./components/LazyErrorBoundary";
import { MetricCard } from "./components/MetricCard";
import { RealMap } from "./components/RealMap";
import { SignalsAlertsCard } from "./components/SignalsAlertsCard";
import { TopMoversCard } from "./components/TopMoversCard";
import { UpcomingEventsCard } from "./components/UpcomingEventsCard";
// 模态 / popover / ReportPanel / SettingsPage / logout/resolve/staff-group api 已随 CockpitOverlays 抽到 CockpitApp.Sections.tsx。
import { buildApiUrl } from "../../../services/http";
import { listEvents } from "../../../services/vkpi/events-api";
import { getDealerLocations } from "../../../services/vkpi/dealers-api";
import { normalizeEventsHierarchy, normalizeDealersHierarchy } from "./normalizers";
import { I18nContext, makeT } from "./lib/i18n";
import { loadKpiScopeForStaff, saveKpiScopeForStaff } from "./lib/kpiScopeStorage";
import { loadStoredState, saveStoredState } from "./lib/storage";
import { useCockpitRuntime } from "./useCockpitRuntime";
import { createProject, deleteProject, updateProject } from "../../../services/vkpi/projects-api";
import { addProjectCost } from "../../../services/vkpi/cost-api";
import { toUiStaffList } from "../../../services/vkpi/staffAdapter";
import { listStaffGroups, toUiGroup } from "../../../services/vkpi/groups-api";
import { KPI_SCOPES } from "./data/kpiScopes";
import { NAV_ITEMS } from "./data/navItems";
import { VIEW_MODES } from "./data/viewModes";
import { emptyDashboardData } from "../data/emptyDashboardData";
import {
  buildMappedEvents,
  buildUpcomingEvents,
  buildEventPins,
  buildPins,
  buildFocusTarget,
  buildTopListData,
  buildCountryOptions,
  buildCityOptions,
  displayCityLabel,
  buildItemOptions,
  buildVenueOptions,
} from "./CockpitApp.helpers";
import { CockpitOverlays } from "./CockpitApp.Sections";

const e = React.createElement;
// MY KOL 改版 M1(2026-07-11):导航项改挂板块页范式新族 MyKolBoardPage(可编辑看板)。
// 旧 pages/myKol/MyKolPage.tsx 保留不删(回滚垫:把本行 import 指回 ../pages/myKol/MyKolPage 即回滚)。
const MyKolBoardPage = React.lazy(() => import("./pages/MyKolBoardPage").then((module) => ({ default: module.MyKolBoardPage })));
const LegacyProjectsPage = React.lazy(() => import("./pages/ProjectsBoardPage").then((module) => ({ default: module.ProjectsBoardPage })));
// 回滚垫:改回 ../pages/ProjectsPage + module.ProjectsPage 即回旧 Projects
const EventsMockupPage = React.lazy(() => import("./pages/EventsBoardPage").then((module) => ({ default: module.EventsBoardPage })));
// 回滚垫:改回 ../pages/events/EventsMockupPage + module.EventsMockupPage 即回旧 Events
// L1(2026-06-30):Wave1-4 已建运维页接进 cockpit 壳(原硬白名单够不到 → 点不到)。
const DataQualityPage = React.lazy(() => import("../pages/DataQualityPage").then((module) => ({ default: module.DataQualityPage })));
const DataQueryPage = React.lazy(() => import("../pages/DataQueryPage").then((module) => ({ default: module.DataQueryPage })));
const MarketTrendsPage = React.lazy(() => import("../pages/MarketTrendsPage").then((module) => ({ default: module.MarketTrendsPage })));
const SkillStudioPage = React.lazy(() => import("../pages/SkillStudioPage").then((module) => ({ default: module.SkillStudioPage })));
const IntelligentPage = React.lazy(() => import("./pages/IntelligentPage").then((module) => ({ default: module.IntelligentPage })));
const ReplyQueuePage = React.lazy(() => import("./pages/ReplyQueueBoardPage").then((module) => ({ default: module.ReplyQueueBoardPage })));
// 回滚垫:改回 ../pages/ReplyQueuePage + module.ReplyQueuePage 即回旧回复队列
// 第2轮 档案工程:SKU 360°(产品视角)+ KOL 完整档案(八层组装页)
const Sku360Page = React.lazy(() => import("./pages/Sku360BoardPage").then((module) => ({ default: module.Sku360BoardPage })));
// 回滚垫:改回 ./pages/Sku360Page + module.Sku360Page 即回旧 SKU360
const KolProfilePage = React.lazy(() => import("./pages/KolProfileBoardPage").then((module) => ({ default: module.KolProfileBoardPage })));
// 回滚垫:改回 ./pages/KolProfilePage + module.KolProfilePage 即回旧档案页
// 第4轮 发射台:新品 SKU 一键六输出全案
const LaunchPadPage = React.lazy(() => import("./pages/LaunchPadBoardPage").then((module) => ({ default: module.LaunchPadBoardPage })));
// 回滚垫:改回 ./pages/LaunchPadPage + module.LaunchPadPage 即回旧发射台
// 第5轮 自治层:驾照板 + 市场之声月报
const AutonomyBoardPage = React.lazy(() => import("./pages/AutonomyBoardPage").then((module) => ({ default: module.AutonomyBoardPage })));
const MarketVoicePage = React.lazy(() => import("./pages/MarketVoicePage").then((module) => ({ default: module.MarketVoicePage })));
// 第6轮 P6 飞轮:段级创意资产库
const CreativeLibraryPage = React.lazy(() => import("./pages/CreativeLibraryBoardPage").then((module) => ({ default: module.CreativeLibraryBoardPage })));
// 回滚垫:改回 ./pages/CreativeLibraryPage + module.CreativeLibraryPage 即回旧创意库
// 战略大脑波:战略台(对照/赛道/模拟/表现 四块合屏)
const StrategyBoardPage = React.lazy(() => import("./pages/StrategyBoardPage").then((module) => ({ default: module.StrategyBoardPage })));
// GTM-1 总脑:上市增长指挥图
const GtmCommandPage = React.lazy(() => import("./pages/GtmCommandBoardPage").then((module) => ({ default: module.GtmCommandBoardPage })));
// 回滚垫:改回 ./pages/GtmCommandPage + module.GtmCommandPage 即回旧 GTM

// L1:cockpit 壳可达的板块 key 白名单(原硬编码在 useState 初值里;运维页加入后集中维护)。
const COCKPIT_BOARDS = [
  "dashboard", "kol-pool", "my-kol", "projects", "events", "shopify", "dealers",
  "triage", "dataQuery", "marketTrends", "skillStudio",
  "intelligent", "replyQueue",
  "sku360", "kolProfile", "launchpad", "autonomy", "marketVoice", "creativeLibrary", "strategyBoard", "gtmCommand",
] as const;

export function CockpitApp(props: any = {}) {
  const {
    apiToken,
    userName,
    userRole,
    userAvatar,
    userEmail = "",
    userAuthRole = "",
    data: dashboardData = emptyDashboardData,
    viewMode: appViewMode = "manager",
    onRefreshData,
    onSelectPage,
    onToggleView,
    onLookupKol,
    onUpsertProjectTerms,
    onUploadEvidenceFile,
    onAddProjectShipment,
    onMoveProjectStage,
    onSignOut,
  } = props;
  // V6.10: 从 localStorage 恢复
  const stored = loadStoredState();
  const urlNav = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("cockpit") : "";
  const urlReport = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("report") : "";
  const normalizeReplicaNav = (key: any) => key === "discover" || key === "channels" ? "my-kol" : key;
  const initialNav = normalizeReplicaNav(urlNav) || normalizeReplicaNav(stored.activeNav) || "dashboard";
  
  const [collapsed, setCollapsed] = useState(stored.collapsed || false);
  const [activeNav, setActiveNav] = useState((COCKPIT_BOARDS as readonly string[]).includes(initialNav) ? initialNav : "dashboard");
  const [dashboardEditing, setDashboardEditing] = useState(false);
  useEffect(() => {
    if (activeNav !== "dashboard" && dashboardEditing) setDashboardEditing(false);
  }, [activeNav, dashboardEditing]);
  useEffect(() => {
    // A focused sidebar item can re-anchor the document after the route paint,
    // so reset once immediately and once after layout has settled.
    const resetPageScroll = () => {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    };
    resetPageScroll();
    const frame = window.requestAnimationFrame(resetPageScroll);
    return () => window.cancelAnimationFrame(frame);
  }, [activeNav]);
  // 主题统一(2026-07):cockpit 不再自持 theme,改吃全局 ThemeProvider(<html> data-theme/
  // data-style,localStorage vkpi-ui-pref-v1),让 玻璃/仪器/单色 × 明暗 与全站一致。
  // setTheme 桥接:兼容「传值」与「函数式更新」两种既有调用(侧栏传值 / 用户菜单函数式)。
  const { theme: gTheme, setTheme: gSetTheme } = useTheme();
  const theme = gTheme;
  const setTheme = useCallback((next: any) => {
    gSetTheme(typeof next === "function" ? next(gTheme) : next);
  }, [gTheme, gSetTheme]);

  // 板块授权守卫:stored/程序化导航落到「被该成员隐藏」的板块时弹回 dashboard。
  // 侧栏已隐藏入口(CockpitSidebar 按 canViewBoard 过滤),此处为 stored state / 直链兜底。owner 全见不受影响。
  const boardPerms = usePermissions();
  useEffect(() => {
    if (!boardPerms.canViewBoard(activeNav)) setActiveNav("dashboard");
  }, [activeNav]); // eslint-disable-line react-hooks/exhaustive-deps

  // 浏览器内本地协助:员工开着页面时后台领「安全轻活」(评论清洗等)在本机算,分担服务器算力。
  // 默认关(deploy dark),localStorage/构建期开关开启;只领纯计算任务、只在页面可见时跑、失败静默。
  useBrowserAssist(isBrowserAssistEnabled());

  // 版本徽标:启动时拉一次 /health,展示 server 短 sha 与前后端同步状态(纯只读,失败静默)
  const [versionBadge, setVersionBadge] = useState<any>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(buildApiUrl("/health"), { credentials: "same-origin" });
        if (!res.ok) return;
        const data = await res.json();
        const build = (data && data.build) || {};
        if (!cancelled) {
          setVersionBadge({
            shortSha: String(build.git_short_sha || "").slice(0, 8),
            inSync: Boolean(build.client_matches_server),
            hasClient: Boolean(build.client_build),
          });
        }
      } catch {
        /* /health 不可达时静默,不影响壳层 */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 在线状态(presence)心跳:挂载即打一次,之后每 60s ping /me(后端节流 >50s 才写库),
  // 维持自己的 last_seen_at 在 5 分钟在线窗口内。纯只读副作用,失败静默。
  useEffect(() => {
    if (!apiToken) return;
    let stopped = false;
    const beat = () => {
      fetch(buildApiUrl("/api/auth/me"), {
        credentials: "same-origin",
        headers: { Authorization: `Bearer ${apiToken}` },
      }).catch(() => { /* 静默 */ });
    };
    beat();
    const id = setInterval(() => { if (!stopped) beat(); }, 60000);
    return () => { stopped = true; clearInterval(id); };
  }, [apiToken]);

  useEffect(() => {
    const handleOpenKolSearchSession = () => {
      setActiveNav("kol-pool");
    };
    // 从哪发起回哪去(2026-06-12):账号分析任务点开切回 MY KOL(行定位由 MyKolPage 自取 pending key)
    const handleOpenMyKolKol = () => {
      setActiveNav("my-kol");
    };
    // 2026-06-12 波5 R5:泳道 target_type=project 的任务点开 → 直达项目详情(复用 Active Campaigns 同款管道)
    const handleOpenProjectTask = (event: any) => {
      const projectId = String(event?.detail?.projectId || "");
      if (projectId) setOpenLegacyProjectId(projectId);
      setActiveNav("projects");
    };
    // item1(2026-06-16):video/账号档案任务点开 → 切到 KOL Pool 板块(pending id 由 board 写 localStorage,
    // KOLPoolPage 挂载后消费并按 id 开抽屉)。
    const handleOpenKolPoolItem = () => {
      setActiveNav("kol-pool");
    };
    // 顶栏全局搜索(2026-07 P0 接真):关键词已写 localStorage,这里只负责切板块;
    // KOLPoolPage 挂载/事件时消费 vkpi:pending-kolpool-search 并填入本地筛选。
    const handleOpenKolPoolSearch = () => {
      setActiveNav("kol-pool");
    };
    // 第2轮 档案工程:任意处派发 vkpi:open-kol-profile(id 先写 sessionStorage vkpi:kol-profile-id)→ 切档案页。
    const handleOpenKolProfile = () => {
      setActiveNav("kolProfile");
    };
    window.addEventListener("vkpi:open-kol-profile", handleOpenKolProfile);
    window.addEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
    window.addEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
    window.addEventListener("vkpi:open-project-task", handleOpenProjectTask);
    window.addEventListener("vkpi:open-kol-pool-item", handleOpenKolPoolItem);
    window.addEventListener("vkpi:open-kol-pool-search", handleOpenKolPoolSearch);
    return () => {
      window.removeEventListener("vkpi:open-kol-profile", handleOpenKolProfile);
      window.removeEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
      window.removeEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
      window.removeEventListener("vkpi:open-project-task", handleOpenProjectTask);
      window.removeEventListener("vkpi:open-kol-pool-item", handleOpenKolPoolItem);
      window.removeEventListener("vkpi:open-kol-pool-search", handleOpenKolPoolSearch);
    };
  }, []);
  
  // 层级 state
  const [viewMode, setViewMode]   = useState(stored.viewMode || null);
  const [country, setCountry]     = useState(stored.country || "");
  const [city, setCity]           = useState(stored.city || "");
  const [item, setItem]           = useState(stored.item || "");
  const [venue, setVenue]         = useState(stored.venue || "");

  const [selectedPin, setSelectedPin] = useState<any>(null);
  const [selectedLegacyProject, setSelectedLegacyProject] = useState<any>(null);
  const [openLegacyProjectId, setOpenLegacyProjectId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<any>(null);
  const [selectedKpi, setSelectedKpi] = useState<any>(null);
  const [previewEvent, setPreviewEvent] = useState<any>(null);
  // 从 dashboard「查看完整报告/编辑」跳到 Events 页时,带上要自动打开的活动 id。
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  // 2026-06-14 诚实化:Upcoming Events 卡接真实 /api/admin/vkpi/events,不再传空数组。
  const [eventRows, setEventRows] = useState<any[]>([]);
  const [dealerPins, setDealerPins] = useState<any[]>([]);
  // D4 核对(2026-07-02):KPI scope(All/KOL/公司)已有持久化 —— 初始化读 loadStoredState().kpiScope,
  // 变更由下方 saveStoredState effect 写进 localStorage["vkpi-dashboard-state-v1"] 统一状态包;
  // 无需再开独立的 "vkpi:kpi-scope" 键(避免双份来源打架)。
  const [kpiScope, setKpiScope] = useState(stored.kpiScope || "all");
  const [reportOpen, setReportOpen] = useState(urlReport === "1"); // V6.10: Report Panel
  const [selectedSignal, setSelectedSignal] = useState<any>(null); // V6.11: Signal detail modal
  // V6.13: 新 modal states
  const [selectedProject, setSelectedProject] = useState<any>(null); // Active Campaigns 项目详情
  const [selectedPublish, setSelectedPublish] = useState<any>(null); // 7 天日历某条
  const [selectedMover, setSelectedMover] = useState<any>(null);     // Top Movers 单条
  const [showAllSignals, setShowAllSignals] = useState(false);  // Signals View All
  const [showAIConfirm, setShowAIConfirm] = useState(false);    // AI Today Approve 确认
  const [aiRegenerating, setAiRegenerating] = useState(false);  // AI Today Regenerate loading
  // V6.14: 顶部 4 按钮 popover state
  const [showHelp, setShowHelp] = useState(false);
  const [showMessages, setShowMessages] = useState(false);
  const [showNotifs, setShowNotifs] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  // V6.14.1: button refs for anchored popovers
  const helpBtnRef = useRef(null);
  const messagesBtnRef = useRef(null);
  const notifsBtnRef = useRef(null);
  const userMenuBtnRef = useRef(null);
  // V6.14.2: i18n + role
  const [lang, setLang] = useState("zh");
  const t = useMemo(() => makeT(lang), [lang]);
  const [viewingAs, setViewingAs] = useState<any>(null);  // Admin 切换查看身份
  const {
    currentUser,
    runtimeNotifications,
    setRuntimeNotifications,
    runtimeReminders,
    kolPoolRows,
    kolPoolLoading,
    kolPoolError,
    dashboardRuntime,
    dashboardLoading,
    dashboardError,
  } = useCockpitRuntime({ apiToken, userName, userRole, userAvatar, userEmail, userAuthRole, starredProjects: dashboardData.starredProjects || [] });
  const activeStaffId = viewingAs ? viewingAs.id : currentUser.id;
  // 【D4】KPI scope 记忆(per-staff):身份就绪(真实 staff id 从 shell bundle 回来,>0)后,
  // 读回该员工上次选择的 scope(键带 staff id 防同浏览器多账号串号);此后变更同步写 per-staff 键。
  // 挂载初值仍走旧共享键 stored.kpiScope(身份未知时的兜底),per-staff 值就绪后覆盖之。
  // 注意用 currentUser.id(真实登录人)而非 viewingAs——管理员「以他人视角查看」不该改写别人的记忆。
  const kpiScopeStaffId = Number(currentUser?.id) || 0;
  useEffect(() => {
    if (!kpiScopeStaffId) return;
    const remembered = loadKpiScopeForStaff(kpiScopeStaffId);
    if (remembered) setKpiScope(remembered);
  }, [kpiScopeStaffId]);
  useEffect(() => {
    if (!kpiScopeStaffId) return;
    saveKpiScopeForStaff(kpiScopeStaffId, kpiScope);
  }, [kpiScopeStaffId, kpiScope]);
  const activeReminders = useMemo(() => viewingAs ? [] : runtimeReminders, [viewingAs, runtimeReminders]);
  // Real staff (17) adapted to the UI shape the team/group/events modals expect.
  const uiStaff = useMemo(() => toUiStaffList(dashboardData.staffMembers || []), [dashboardData.staffMembers]);
  // Real staff-groups loaded from the new backend (replaces hardcoded "KOL Operations").
  const [staffGroups, setStaffGroups] = useState<any[]>([]);
  const refreshStaffGroups = useCallback(async () => {
    if (!apiToken) return;
    try {
      const res = await listStaffGroups(apiToken);
      setStaffGroups((res.items || []).map(toUiGroup));
    } catch (err) {
      setStaffGroups([]);
    }
  }, [apiToken]);
  useEffect(() => { refreshStaffGroups(); }, [refreshStaffGroups]);
  // The group currently targeted by the editor (edit mode binds to a real group).
  const [editGroupTarget, setEditGroupTarget] = useState<any>(null);
  const reportData = useMemo(() => ({
    currentUser,
    dashboard: dashboardRuntime,
    kolPoolRows,
    notifications: runtimeNotifications,
    reminders: runtimeReminders,
    errors: { dashboardError, kolPoolError },
  }), [currentUser, dashboardRuntime, kolPoolRows, runtimeNotifications, runtimeReminders, dashboardError, kolPoolError]);
  const openGroupEditor = (mode = "edit", group = null) => {
    setEditGroupMode(mode);
    const target = group || (mode === "edit" ? staffGroups[0] : null);
    setEditGroupTarget(target || null);
    setEditGroupName(mode === "new" ? "新分组" : (target?.name || "新分组"));
    setShowEditGroup(true);
  };
  const pushLocalNotification = (notification: any) => {
    setRuntimeNotifications(prev => [notification, ...prev].slice(0, 80));
  };
  const handleFeedbackSubmitted = (result: any, payload: any) => {
    const alert = result?.alert || {};
    pushLocalNotification({
      id: alert.id || result?.feedback?.uid || `feedback-${Date.now()}`,
      raw: alert,
      iconKey: "warning",
      iconColor: "#f59e0b",
      title: alert.title || `用户反馈待处理: ${payload.title}`,
      desc: alert.body || "已发送到管理通知列表",
      time: "刚刚",
      unread: true,
      category: "feedback",
      severity: payload.category === "bug" ? "high" : "medium",
      status: "todo",
      priority: payload.category === "bug" ? "high" : "medium",
      source: "vkpi_feedback",
    });
  };
  const handleReplicaSelectPage = (page: any) => {
    if (page === "channels" || page === "discover") {
      setActiveNav("my-kol");
      return;
    }
    if (page === "kolPoolV2" || page === "kol-pool") {
      setActiveNav("kol-pool");
      return;
    }
    if (page === "projects") {
      setActiveNav("projects");
      return;
    }
    if (page === "events") {
      setActiveNav("events");
      return;
    }
    if (page === "command") {
      onToggleView?.("command");
      return;
    }
    if (page === "cockpit" || page === "dashboardPremium") {
      setActiveNav("dashboard");
      return;
    }
    onSelectPage?.(page);
  };
  const refreshReplicaProjects = useCallback(async () => {
    await onRefreshData?.();
  }, [onRefreshData]);
  const handleReplicaCreateProject = useCallback(async (payload: any) => {
    if (!apiToken) throw new Error("缺少 API token，不能创建项目。");
    const visiblePayload = payload?.sourceType === "cockpit_projects_ui"
      ? { ...payload, sourceType: "excel_promo_plan" }
      : payload;
    const result = await createProject(apiToken, visiblePayload);
    // 创建已 200 成功;刷新列表失败不该被上层 catch 当成"创建失败"(此前的假失败真因)。
    try { await refreshReplicaProjects(); } catch { /* 刷新打嗝不影响创建成功 */ }
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaUpdateProject = useCallback(async (projectId: any, payload: any) => {
    if (!apiToken) throw new Error("缺少 API token，不能更新项目。");
    const result = await updateProject(apiToken, projectId, payload);
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaDeleteProject = useCallback(async (projectId: any, reason: any) => {
    if (!apiToken) throw new Error("缺少 API token，不能取消项目。");
    const result = await deleteProject(apiToken, projectId, reason || "用户在 Cockpit Projects 取消项目");
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaAddProjectCost = useCallback(async (payload: any) => {
    if (!apiToken) throw new Error("缺少 API token，不能登记费用。");
    const result = await addProjectCost(apiToken, payload);
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  // 7 子 modals
  const [showProfile, setShowProfile] = useState(false);
  const [showTeam, setShowTeam] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  // 授权页 V1:头像菜单「成员与授权」(仅 owner)直达设置页 staff 区;普通「系统设置」重置回默认区。
  const [settingsInitialSection, setSettingsInitialSection] = useState<"staff" | null>(null);
  // 授权页 V1.1:「成员与授权」改直达独立浮层页(AuthorizationOverlay);设置页 staff 分区保留(双入口)。
  const [showMembersAuth, setShowMembersAuth] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  // V6.14.4: ViewAll modals + NotifDetail + EditGroup
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [showAllMovers, setShowAllMovers] = useState(false);
  const [showFullCalendar, setShowFullCalendar] = useState(false);
  const [showAllReminders, setShowAllReminders] = useState(false);
  const [showAllNotifs, setShowAllNotifs] = useState(false);
  const [selectedNotif, setSelectedNotif] = useState<any>(null);
  const [showEditGroup, setShowEditGroup] = useState(false);
  const [editGroupMode, setEditGroupMode] = useState("edit");
  const [editGroupName, setEditGroupName] = useState("KOL Operations");
  const globeContainerRef = useRef(null);

  // 死双轨清理(2026-07-11):body.light 全站零 CSS 消费,主题唯一真源=ThemeProvider 的
  // <html> data-theme;保留此双轨迟早有人写 body.light{} 复活第二套主题源,故删。

  // V6.10: 持久化 state(用户偏好)
  useEffect(() => {
    saveStoredState({ collapsed, activeNav, theme, viewMode, country, city, item, venue, kpiScope });
  }, [collapsed, activeNav, theme, viewMode, country, city, item, venue, kpiScope]);

  // 四-map:KOLs 模式的分布数据(/dashboard/kol-distribution-pack → mapHierarchy)是真算出来的,
  // 有真数据就点亮该模式(available=true);Dealers / Customer Heatmap / Events 仍诚实禁用——
  // 它们各自要 Shopify / dealer / events-on-map 端点(Wave 2),保持 WAITING/待接入 徽标。
  const kolHierarchyReady = Boolean(
    dashboardRuntime.mapHierarchy && Object.keys(dashboardRuntime.mapHierarchy).length > 0,
  );
  // 真实活动地图层(只上图带定位的活动;0 个带定位则诚实保持禁用)。
  const eventsHierarchy = useMemo(() => normalizeEventsHierarchy(eventRows) || {}, [eventRows]);
  const eventsGeoCount = Object.keys(eventsHierarchy).length;
  // 经销商地图层(主页地球):/dealers/locations 扁平 pin → US→cities 层级;0 个带经纬度则诚实禁用。
  const dealersHierarchy = useMemo(() => normalizeDealersHierarchy(dealerPins) || {}, [dealerPins]);
  const dealersGeoCount = Object.keys(dealersHierarchy).length;
  const runtimeViewModes = useMemo(() => ({
    ...VIEW_MODES,
    kols: {
      ...VIEW_MODES.kols,
      desc: kolHierarchyReady ? "真实 KOL Pool 国家分布" : "KOL 分布数据待接入",
      hierarchy: dashboardRuntime.mapHierarchy || {},
      available: kolHierarchyReady,
    },
    dealers: {
      ...VIEW_MODES.dealers,
      desc: dealersGeoCount > 0 ? `${Object.keys((dealersHierarchy as any).US?.cities || {}).length} 城有经销商` : "经销商填经纬度后自动上图",
      hierarchy: dealersHierarchy,
      available: dealersGeoCount > 0,
    },
    customer: {
      ...VIEW_MODES.customer,
      hierarchy: {},
      available: false,
    },
    events: {
      ...VIEW_MODES.events,
      desc: eventsGeoCount > 0 ? `${eventsGeoCount} 地有定位活动` : "活动填城市/国家后自动上图",
      hierarchy: eventsHierarchy,
      available: eventsGeoCount > 0,
    },
  }), [dashboardRuntime.mapHierarchy, kolHierarchyReady, eventsHierarchy, eventsGeoCount, dealersHierarchy, dealersGeoCount]);
  const currentMode = viewMode ? (runtimeViewModes as any)[viewMode] : null;
  const isAvailable = currentMode?.available;
  const hierarchy = currentMode?.hierarchy || {};

  // Country options
  const countryOptions = useMemo(() => buildCountryOptions({ currentMode, hierarchy, viewMode }), [currentMode, hierarchy, viewMode]);

  // City options
  const cityOptions = useMemo(() => buildCityOptions({ country, hierarchy, viewMode }), [country, hierarchy, viewMode]);

  // Item options(KOL / Store)
  const itemOptions = useMemo(() => buildItemOptions({ city, country, hierarchy }), [city, country, hierarchy]);

  // V4.5: Venue options(街道级 / 店内楼层 / Landmark)
  const venueOptions = useMemo(() => buildVenueOptions({ item, city, country, hierarchy }), [item, city, country, hierarchy]);

  // 真实 events → 统一 UI 形态(Upcoming 卡 / 地图落点 / preview 共用同一映射)。
  // 定义在 pins useMemo 之前:pins 在 events 视图引用 eventPins。
  const mappedEvents = useMemo(() => buildMappedEvents(eventRows), [eventRows]);

  // Upcoming 卡:start_date >= 今天,升序,取前 6。
  // 时区安全:用本地 YYYY-MM-DD 字符串比较(Date.parse 把 date-only 当 UTC,
  // 在 UTC- 时区会把"今天"的活动算成昨天而漏掉 → 之前 Upcoming(0) 的真因)。
  const upcomingEvents = useMemo(() => buildUpcomingEvents(mappedEvents), [mappedEvents]);

  // 地图落点:每个「有定位」(显式经纬度或可识别国家)的活动一个 pin,点击弹 preview。
  // 无定位的活动诚实不上图(不会凭空造点)。country 统一大写,与 pins 过滤口径一致。
  const eventPins = useMemo(() => buildEventPins(mappedEvents), [mappedEvents]);

  // 计算当前要在地球上显示的 pins
  const pins = useMemo(
    () => buildPins({ currentMode, isAvailable, country, city, item, venue, hierarchy, eventPins }),
    [currentMode, isAvailable, country, city, item, venue, hierarchy, eventPins],
  );

  // 计算 focusTarget
  const focusTarget = useMemo(
    () => buildFocusTarget({ country, city, item, venue, hierarchy, currentMode }),
    [country, city, item, venue, hierarchy, currentMode],
  );

  // 面包屑路径
  const breadcrumb = useMemo(() => {
    const arr = [];
    if (country) arr.push(country);
    if (city) arr.push(displayCityLabel(city));
    if (item) arr.push(item);
    if (venue) arr.push(venue);
    return arr;
  }, [country, city, item, venue]);

  // 返回上一级
  const goBack = () => {
    if (venue) setVenue("");
    else if (item) setItem("");
    else if (city) setCity("");
    else if (country) setCountry("");
  };

  // 切换上级自动清空下层
  const handleCountryChange = (c: any) => {
    setCountry(c); setCity(""); setItem(""); setVenue("");
  };
  const handleCityChange = (c: any) => {
    setCity(c); setItem(""); setVenue("");
  };
  const handleItemChange = (i: any) => {
    setItem(i); setVenue("");
  };

  // Top List 数据(根据当前层级动态生成)
  const topListData = useMemo(
    () => buildTopListData({ currentMode, country, city, item, hierarchy, viewMode }),
    [currentMode, country, city, item, hierarchy, viewMode],
  );

  // 2026-06-14 诚实化:拉真实 events(只读,失败/无 token 静默置空,绝不硬编码假活动)。
  useEffect(() => {
    if (!apiToken) {
      setEventRows([]);
      return;
    }
    let cancelled = false;
    listEvents(apiToken, { limit: 100 })
      .then((res) => {
        if (cancelled) return;
        setEventRows(Array.isArray(res?.items) ? res.items : []);
      })
      .catch(() => {
        if (!cancelled) setEventRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  // 经销商位置(主页地球 dealers 层):拉 /dealers/locations 扁平 pin;失败/无 token 静默置空。
  useEffect(() => {
    if (!apiToken) { setDealerPins([]); return; }
    let cancelled = false;
    getDealerLocations(apiToken)
      .then((res) => { if (!cancelled) setDealerPins(Array.isArray(res?.pins) ? res.pins : []); })
      .catch(() => { if (!cancelled) setDealerPins([]); });
    return () => { cancelled = true; };
  }, [apiToken]);

  // mappedEvents / upcomingEvents / eventPins 已上移到 pins useMemo 之前(被 pins 引用,
  // 必须先于其定义,否则 events 视图下 pins 工厂触发 eventPins 的 TDZ)。

  // 跳到 Events 页(可选自动打开某活动详情)。复用 onOpenProjectsList 等同款 storage + setActiveNav 模式。
  const openEventsPage = useCallback((eventId: any = null) => {
    setPendingEventId(eventId ? String(eventId) : null);
    saveStoredState({ activeNav: "events" });
    setActiveNav("events");
  }, []);

  return e(LazyMotion, { features: domMax },
   e(I18nContext.Provider, { value: { t, lang, setLang } },
   e("div", { className: "relative min-h-screen bg-bg text-ink-2" },
    e("div", {
      className: "pointer-events-none fixed inset-0",
      style: { background: "linear-gradient(180deg, var(--ds-bg-2), var(--ds-bg) 42%) fixed" }
    }),

    // ─── Overlays ───(保守拆:全部模态/popover 挂载抽到 CockpitApp.Sections.tsx 的 CockpitOverlays,
    // 以显式 props 透传所有 state/setter/派生值;返回节点数组,在此原位展开,JSX 行为不变)
    ...CockpitOverlays({
      selectedPin, setSelectedPin, currentMode, setActiveNav,
      selectedEvent, setSelectedEvent, openEventsPage,
      selectedKpi, setSelectedKpi, kpiScope, dashboardRuntime,
      reportOpen, setReportOpen, reportData, apiToken,
      selectedSignal, setSelectedSignal,
      selectedProject, setSelectedProject, uiStaff, currentUser, onRefreshData,
      dashboardData, setSelectedLegacyProject, setOpenLegacyProjectId,
      selectedPublish, setSelectedPublish,
      selectedMover, setSelectedMover,
      showAllSignals, setShowAllSignals,
      showAIConfirm, setShowAIConfirm, pushLocalNotification,
      showHelp, setShowHelp, helpBtnRef, t, setShowShortcuts, setShowFeedback,
      showMessages, setShowMessages, activeReminders, messagesBtnRef, viewingAs, setShowAllReminders,
      showNotifs, setShowNotifs, runtimeNotifications, notifsBtnRef, setShowAllNotifs, setSelectedNotif,
      showUserMenu, setShowUserMenu, theme, setTheme, userMenuBtnRef, lang, setLang,
      setViewingAs, setShowProfile, setShowTeam, setShowSettingsModal, onSignOut,
      showProfile, showTeam, staffGroups, openGroupEditor,
      showSettingsModal, appViewMode,
      settingsInitialSection, setSettingsInitialSection, isOwnerUser: boardPerms.isOwner(),
      showMembersAuth, setShowMembersAuth,
      showShortcuts, showFeedback, handleFeedbackSubmitted,
      showAllProjects, setShowAllProjects,
      showAllMovers, setShowAllMovers,
      showFullCalendar, setShowFullCalendar,
      showAllReminders,
      showAllNotifs,
      selectedNotif, setRuntimeNotifications,
      showEditGroup, setShowEditGroup, editGroupName, editGroupMode, editGroupTarget, refreshStaffGroups,
      previewEvent, setPreviewEvent, mappedEvents,
    }),

    e("div", { className: "cockpit-shell relative flex min-h-screen w-full" },

      // ─── 移动端导航 ───(< md:桌面侧边栏隐藏,此处补汉堡+滑出抽屉;≥ md 不渲染)
      e(CockpitMobileNav, { activeNav, setActiveNav }),

      // ─── Sidebar ───(保守拆:纯展示叶子区块抽到 CockpitSidebar,props 显式传递,行为不变)
      e(CockpitSidebar, {
        collapsed, setCollapsed, activeNav, setActiveNav, theme, setTheme, versionBadge, apiToken,
      }),

      // ─── Main ───
      e("main", { className: "min-w-0 flex-1" },

        // Header(保守拆:纯展示顶栏抽到 CockpitTopbar,props 显式传递,行为不变)
        e(CockpitTopbar, {
          activeNav, helpBtnRef, setShowHelp, messagesBtnRef, setShowMessages, activeReminders,
          setReportOpen, notifsBtnRef, setShowNotifs, runtimeNotifications, userMenuBtnRef,
          setShowUserMenu, viewingAs, currentUser, t,
          apiToken,
          onNavigate: setActiveNav,
          dashboardEditing,
          setDashboardEditing,
        }),

        // ─── ROUTING ───
        e(AnimatePresence, { mode: "wait", initial: false },
          e(m.div, {
            key: activeNav,
            className: `vkpi-page-stage vkpi-page-stage--${activeNav} min-h-[calc(100vh-4rem)]`,
            initial: { opacity: 0, y: 14 },
            animate: { opacity: 1, y: 0 },
            exit: { opacity: 0, y: -10 },
            transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] },
          },
            activeNav === "kol-pool" && e(KOLPoolPage, {
              items: kolPoolRows,
              loading: kolPoolLoading,
              error: kolPoolError,
              apiToken,
              staff: uiStaff,
            }),

            activeNav === "my-kol" && e(React.Suspense, {
              fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "MY KOL 加载中...")
            },
              e(MyKolBoardPage, {
                apiToken,
                viewMode: appViewMode,
                data: dashboardData,
                userName,
                userRole,
                onRefreshData,
                onSelectPage: handleReplicaSelectPage,
                // K3 内容播放实测:复用主控已拉的 evidence_metrics(零重复请求/零 lineage 隐藏写入)
                metrics: dashboardRuntime.metrics,
              })
            ),

            activeNav === "projects" && e(React.Suspense, {
              fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Projects 加载中...")
            },
              e(LegacyProjectsPage as React.ComponentType<any>, {
                data: dashboardData,
                filteredProjects: dashboardData.projects || [],
                selectedProjectId: selectedLegacyProject?.id,
                selectedProject: selectedLegacyProject || (dashboardData.projects || [])[0],
                openProjectId: openLegacyProjectId,
                viewMode: appViewMode,
                apiToken,
                onSelectProject: setSelectedLegacyProject,
                // 2026-06-12 波5 R3:此前为空函数死点击;最小接真 → 跳 KOL Pool 页
                onOpenKolProfile: () => {
                  saveStoredState({ activeNav: "kol-pool" });
                  setActiveNav("kol-pool");
                },
                onOpenStaffProfile: () => undefined,
                onSelectPage: handleReplicaSelectPage,
                onToggleView,
                onRefreshData,
                onLookupKol,
                onUpsertProjectTerms,
                onUploadEvidenceFile,
                onAddProjectShipment,
                onMoveProjectStage,
                onCreateProject: handleReplicaCreateProject,
                onUpdateProject: handleReplicaUpdateProject,
                onDeleteProject: handleReplicaDeleteProject,
                onAddProjectCost: handleReplicaAddProjectCost,
              })
            ),

            activeNav === "events" && e(LazyErrorBoundary, { name: "Events" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Events 加载中...")
              },
                e(EventsMockupPage, {
                  userName,
                  staff: uiStaff,
                  currentUser,
                  // dashboard 跳转带来的活动 id:打开后即清空,避免返回再进又自动弹。
                  initialEventId: pendingEventId,
                  onConsumeInitialEvent: () => setPendingEventId(null),
                })
              )
            ),

            activeNav === "shopify" && e(ShopifyHubPage as React.ComponentType<any>, { apiToken }),
            activeNav === "dealers" && e(DealerMapPage, { apiToken }),

            // L1:智能运维组的 4 个 Wave1-4 页(各自只读自取数据;失败/无 token 静默)。
            //   triage 复用 DataQualityPage(运维页宿主,viewMode=manager 才拉质量摘要)。
            activeNav === "triage" && e(LazyErrorBoundary, { name: "Triage" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "运维 Triage 加载中...")
              },
                e(DataQualityPage as React.ComponentType<any>, { apiToken, viewMode: "manager" })
              )
            ),
            activeNav === "dataQuery" && e(LazyErrorBoundary, { name: "DataQuery" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "问数 加载中...")
              },
                e(DataQueryPage as React.ComponentType<any>, { apiToken })
              )
            ),
            activeNav === "marketTrends" && e(LazyErrorBoundary, { name: "MarketTrends" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "市场趋势 加载中...")
              },
                e(MarketTrendsPage as React.ComponentType<any>, { apiToken })
              )
            ),
            activeNav === "skillStudio" && e(LazyErrorBoundary, { name: "SkillStudio" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Skill Studio 加载中...")
              },
                e(SkillStudioPage as React.ComponentType<any>, { apiToken })
              )
            ),
            // P1 智能可见周:Intelligent 问答(三车道)+ 回复队列(评论区销售员 v0 半自动)
            activeNav === "intelligent" && e(LazyErrorBoundary, { name: "Intelligent" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Intelligent 问答加载中...")
              },
                e(IntelligentPage as React.ComponentType<any>, { apiToken, onNavigate: setActiveNav })
              )
            ),
            activeNav === "replyQueue" && e(LazyErrorBoundary, { name: "ReplyQueue" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "回复队列加载中...")
              },
                e(ReplyQueuePage as React.ComponentType<any>, { apiToken })
              )
            ),
            // 第2轮 档案工程:SKU 360° + KOL 完整档案
            activeNav === "sku360" && e(LazyErrorBoundary, { name: "Sku360" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "SKU 360° 加载中...")
              },
                e(Sku360Page as React.ComponentType<any>, { apiToken, onNavigate: setActiveNav })
              )
            ),
            activeNav === "kolProfile" && e(LazyErrorBoundary, { name: "KolProfile" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "KOL 档案加载中...")
              },
                e(KolProfilePage as React.ComponentType<any>, { apiToken, onNavigate: setActiveNav })
              )
            ),
            // 第4轮 发射台
            activeNav === "launchpad" && e(LazyErrorBoundary, { name: "LaunchPad" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "发射台加载中...")
              },
                e(LaunchPadPage as React.ComponentType<any>, { apiToken })
              )
            ),
            // 第5轮 自治层:驾照板 + 市场之声
            activeNav === "autonomy" && e(LazyErrorBoundary, { name: "Autonomy" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "自治驾照加载中...")
              },
                e(AutonomyBoardPage as React.ComponentType<any>, { apiToken })
              )
            ),
            activeNav === "marketVoice" && e(LazyErrorBoundary, { name: "MarketVoice" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "市场之声加载中...")
              },
                // onNavigate:溯源身份跳(kol → KOL 档案 / owned → MY KOL 官号矩阵)
                e(MarketVoicePage as React.ComponentType<any>, { apiToken, onNavigate: setActiveNav })
              )
            ),
            // 第6轮 创意资产库
            activeNav === "creativeLibrary" && e(LazyErrorBoundary, { name: "CreativeLibrary" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "创意资产库加载中...")
              },
                e(CreativeLibraryPage as React.ComponentType<any>, { apiToken })
              )
            ),
            // 战略大脑波:战略台
            activeNav === "strategyBoard" && e(LazyErrorBoundary, { name: "StrategyBoard" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "战略台加载中...")
              },
                e(StrategyBoardPage as React.ComponentType<any>, { apiToken })
              )
            ),
            // GTM-1 总脑:上市增长指挥图
            activeNav === "gtmCommand" && e(LazyErrorBoundary, { name: "GtmCommand" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "GTM Command 加载中...")
              },
                e(GtmCommandPage as React.ComponentType<any>, { apiToken, onNavigate: setActiveNav })
              )
            ),

            // Placeholder for nav items not yet built
            !(COCKPIT_BOARDS as readonly string[]).includes(activeNav) && e("div", { className: "p-8 md:p-16 flex flex-col items-center justify-center text-center min-h-[60vh]" },
              e("div", { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-8 max-w-md w-full" },
                (() => {
                  const navItem = NAV_ITEMS.find(n => n.key === activeNav);
                  const Icon = navItem?.icon;
                  return Icon ? e(Icon, { size: 32, className: "text-slate-500 mx-auto mb-3" }) : null;
                })(),
                e("div", { className: "text-base font-semibold text-white mb-1" }, (NAV_ITEMS.find(n => n.key === activeNav)?.label) || "Page"),
                e("div", { className: "text-[12px] text-slate-400 mb-4" }, "此页面尚未接入,在后续阶段完成。"),
                e("button", {
                  onClick: () => setActiveNav("dashboard"),
                  className: "px-4 py-1.5 rounded-md border border-white/[0.08] bg-white/[0.03] text-[11px] text-slate-300 hover:bg-white/[0.06] hover:text-white"
                }, "← 返回 Dashboard")
              )
            ),

            activeNav === "dashboard" && e(DashboardReplicaPage, {
              dashboardEditing,
              onNavigate: setActiveNav,
              // P2 穿透:设置/成员与授权全屏浮层打开时,dashboard 浮卡/地图 overlay 不渲染
              showSettingsModal: showSettingsModal || showMembersAuth,
              kpiScope, setKpiScope, t, setSelectedKpi, globeContainerRef, isAvailable, pins, currentMode,
              venue, item, city, country, setPreviewEvent, handleCountryChange, handleCityChange, handleItemChange,
              setVenue, setSelectedPin, viewMode, setViewMode, countryOptions, cityOptions, itemOptions, venueOptions,
              breadcrumb, goBack, topListData, setSelectedEvent, setSelectedSignal, setShowAllSignals, setShowAIConfirm,
              setAiRegenerating, aiRegenerating, setSelectedMover, setShowAllMovers, setSelectedProject, setShowAllProjects,
              setSelectedPublish, setShowFullCalendar, focusTarget,
              viewModes: runtimeViewModes,
              metrics: dashboardRuntime.metrics,
              sourceHealth: dashboardRuntime.sourceHealth,
              campaigns: dashboardRuntime.campaigns,
              campaignsMeta: dashboardRuntime.campaignsMeta,
              calendarDays: dashboardRuntime.calendarDays,
              calendarMeta: dashboardRuntime.calendarMeta,
              signals: dashboardRuntime.signals,
              aiInsight: dashboardRuntime.aiInsight,
              topMovers: dashboardRuntime.topMovers,
              kolFunnel: dashboardRuntime.kolFunnel,
              // 2026-06-14 诚实化:Upcoming Events 接真实 listEvents(空则卡内显「暂无活动」)。
              upcomingEvents,
              // Revenue by Source 无真实后端口径 → 维持空,卡片自带 length>0 守卫不渲染(绝不编造收入)。
              revenueBySource: [],
              dashboardLoading,
              dashboardError,
              // P5 系统健康条:透传 token 供其独立拉取真实只读端点
              apiToken,
              onOpenProjectsList: () => {
                saveStoredState({ activeNav: "projects" });
                setActiveNav("projects");
              },
              onOpenMyKol: () => {
                saveStoredState({ activeNav: "my-kol" });
                setActiveNav("my-kol");
              },
              // Upcoming Events 卡「View all events →」→ 跳真实 Events 页。
              onOpenEvents: () => openEventsPage(null),
              // L1:运维健康卡「进入 Triage 队列」→ 切到运维 Triage 板块(DataQuality 宿主)。
              onOpenTriage: () => {
                saveStoredState({ activeNav: "triage" });
                setActiveNav("triage");
              },
            })
          )
        )
    )
  )
  )
  )
  );
}
