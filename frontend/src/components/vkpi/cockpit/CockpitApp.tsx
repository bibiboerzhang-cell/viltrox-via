// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "../../../app/providers/ThemeProvider";
// 车道B LazyMotion 瘦身:全 cockpit 树只用 m.*(motion.* 已全量替换),features 在此顶层
// 注入一次。选 domMax 而非 domAnimation:FloatingCard 的 drag 与 ActivityFeed 的 layout
// 动画都要 domMax 才生效(domAnimation 会静默砍掉拖拽 = 功能回退,不只是动效)。
import { LazyMotion, domMax, m } from "framer-motion";
// 死 import 清理(2026-07-12):18 个 lucide 图标 + 12 个卡组件簇 + KPI_SCOPES 全为拆分残留
// (正文零引用,图标/卡片实际消费方在 CockpitTopbar / CockpitApp.Sections / DashboardReplicaPage)。
import "./styles/mockup.css";
import "./styles/cockpit-reference.css";
import "../styles/vkpi-settings-dark.css";
import { DashboardReplicaPage } from "./DashboardReplicaPage";
import { CockpitSidebar } from "./CockpitSidebar";
import { CockpitMobileNav } from "./CockpitMobileNav";
import { CockpitTopbar } from "./CockpitTopbar";
import { usePermissions } from "../../../hooks/usePermissions";
import { useBrowserAssist, isBrowserAssistEnabled } from "../../../lib/browserAssist/enable";
import { LazyErrorBoundary } from "./components/LazyErrorBoundary";
// 模态 / popover / ReportPanel / SettingsPage / logout/resolve/staff-group api 已随 CockpitOverlays 抽到 CockpitApp.Sections.tsx。
import { listUpcomingEvents } from "../../../services/vkpi/events-api";
import { getDealerLocations } from "../../../services/vkpi/dealers-api";
import { normalizeEventsHierarchy, normalizeDealersHierarchy } from "./normalizers";
import { I18nContext, makeT } from "./lib/i18n";
import { loadKpiScopeForStaff, saveKpiScopeForStaff } from "./lib/kpiScopeStorage";
import { loadStoredState, saveStoredState } from "./lib/storage";
import { resolveDashboardMapSelection } from "./mapViewSelection";
import { useCockpitRuntime } from "./useCockpitRuntime";
import { createProject, deleteProject, updateProject } from "../../../services/vkpi/projects-api";
import { addProjectCost } from "../../../services/vkpi/cost-api";
import { toUiStaffList } from "../../../services/vkpi/staffAdapter";
import { listStaffGroups, toUiGroup } from "../../../services/vkpi/groups-api";
import { NAV_ITEMS } from "./data/navItems";
import { VIEW_MODES } from "./data/viewModes";
import { emptyDashboardData } from "../data/emptyDashboardData";
import {
  buildMappedEvents,
  filterUpcomingEvents,
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
import * as lazyBoards from "./CockpitApp.lazyBoards";
import {
  useCockpitNavigationEvents,
  useCockpitPresenceHeartbeat,
  useCockpitVersionBadge,
} from "./CockpitApp.shellHooks";

const e = React.createElement;
const { COCKPIT_BOARDS, KOLPoolPage, ShopifyBoardPage, DealerMapPage, MyKolBoardPage, LegacyProjectsPage,
  EventsMockupPage, DataQualityPage, DataQueryPage, MarketTrendsPage, SkillStudioPage, IntelligentPage, ReplyQueuePage, Sku360Page, KolProfilePage, LaunchPadPage, AutonomyBoardPage, MarketVoicePage, CreativeLibraryPage, StrategyBoardPage, GtmCommandPage } = lazyBoards;

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
  // Cockpit shares the global style/theme preference and keeps both setter forms compatible.
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

  // Local browser assist is opt-in and only handles safe compute while the page is visible.
  useBrowserAssist(isBrowserAssistEnabled());

  const versionBadge = useCockpitVersionBadge();
  useCockpitPresenceHeartbeat(apiToken);
  
  // 层级 state
  const [viewMode, setViewMode]   = useState(stored.viewMode || null);
  const [country, setCountry]     = useState(stored.country || "");
  const [city, setCity]           = useState(stored.city || "");
  const [item, setItem]           = useState(stored.item || "");
  const [venue, setVenue]         = useState(stored.venue || "");

  const [selectedPin, setSelectedPin] = useState<any>(null);
  const [selectedLegacyProject, setSelectedLegacyProject] = useState<any>(null);
  const [openLegacyProjectId, setOpenLegacyProjectId] = useState("");
  useCockpitNavigationEvents({ setActiveNav, setOpenLegacyProjectId });
  const [selectedEvent, setSelectedEvent] = useState<any>(null);
  const [selectedKpi, setSelectedKpi] = useState<any>(null);
  const [previewEvent, setPreviewEvent] = useState<any>(null);
  // 从 dashboard「查看完整报告/编辑」跳到 Events 页时,带上要自动打开的活动 id。
  const [pendingEventId, setPendingEventId] = useState<string | null>(null);
  // 2026-06-14 诚实化:Upcoming Events 卡接真实 /api/admin/vkpi/events,不再传空数组。
  const [eventRows, setEventRows] = useState<any[]>([]);
  const [dealerPins, setDealerPins] = useState<any[]>([]);
  const [eventMapRequest, setEventMapRequest] = useState({ loading: Boolean(apiToken), error: "" });
  const [dealerMapRequest, setDealerMapRequest] = useState({ loading: Boolean(apiToken), error: "" });
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
    aiTodayRegeneration,
    regenerateAiToday,
  } = useCockpitRuntime({ apiToken, userName, userRole, userAvatar, userEmail, userAuthRole, starredProjects: dashboardData.starredProjects || [] });
  const aiRegenerating = aiTodayRegeneration?.phase === "running";
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

  // 活动日期的唯一前端口径:UTC YYYY-MM-DD，同一值同时传给
  // upcoming API 与本地 fail-closed 过滤，不让浏览器时区再选一个日期。
  const eventAsOfDate = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const mappedEvents = useMemo(() => buildMappedEvents(eventRows), [eventRows]);
  const currentUpcomingEvents = useMemo(
    () => filterUpcomingEvents(mappedEvents, eventAsOfDate),
    [eventAsOfDate, mappedEvents],
  );
  const upcomingEvents = useMemo(
    () => buildUpcomingEvents(mappedEvents, eventAsOfDate),
    [eventAsOfDate, mappedEvents],
  );
  const eventPins = useMemo(() => buildEventPins(currentUpcomingEvents), [currentUpcomingEvents]);

  // 地图模式只由真实层级/坐标数据点亮。请求状态单独保留，避免把“仍在加载”误判为空。
  const kolHierarchyReady = Boolean(
    dashboardRuntime.mapHierarchy && Object.keys(dashboardRuntime.mapHierarchy).length > 0,
  );
  // 真实活动地图层:只消费 upcoming + 精确经纬度。国家字段不伪造点位。
  const eventsHierarchy = useMemo(
    () => normalizeEventsHierarchy(currentUpcomingEvents.map((event) => event.raw)) || {},
    [currentUpcomingEvents],
  );
  const eventsGeoCount = eventPins.length;
  // 经销商地图层(主页地球):/dealers/locations 扁平 pin → US→cities 层级;0 个带经纬度则诚实禁用。
  const dealersHierarchy = useMemo(() => normalizeDealersHierarchy(dealerPins) || {}, [dealerPins]);
  const dealersGeoCount = Object.keys(dealersHierarchy).length;
  const runtimeViewModes = useMemo(() => ({
    ...VIEW_MODES,
    kols: {
      ...VIEW_MODES.kols,
      desc: kolHierarchyReady ? "KOL 档案主国家分布（单档案单主国家）" : "KOL 档案主国家待接入",
      hierarchy: dashboardRuntime.mapHierarchy || {},
      available: kolHierarchyReady,
      loading: !kolHierarchyReady && (dashboardLoading || kolPoolLoading),
      error: !kolHierarchyReady && !dashboardLoading && !kolPoolLoading
        ? [dashboardError, kolPoolError].filter(Boolean).join("；")
        : "",
    },
    dealers: {
      ...VIEW_MODES.dealers,
      desc: dealersGeoCount > 0 ? `${Object.keys((dealersHierarchy as any).US?.cities || {}).length} 城有经销商` : "经销商填经纬度后自动上图",
      hierarchy: dealersHierarchy,
      available: dealersGeoCount > 0,
      loading: dealersGeoCount === 0 && dealerMapRequest.loading,
      error: dealersGeoCount === 0 ? dealerMapRequest.error : "",
    },
    customer: {
      ...VIEW_MODES.customer,
      hierarchy: {},
      available: false,
    },
    events: {
      ...VIEW_MODES.events,
      desc: eventsGeoCount > 0 ? `${eventsGeoCount} 个未结束活动有精确坐标` : "未结束活动需精确经纬度才上图；国家信息仅作聚合",
      hierarchy: eventsHierarchy,
      available: eventsGeoCount > 0,
      loading: eventsGeoCount === 0 && eventMapRequest.loading,
      error: eventsGeoCount === 0 ? eventMapRequest.error : "",
    },
  }), [
    dashboardRuntime.mapHierarchy,
    kolHierarchyReady,
    dashboardLoading,
    kolPoolLoading,
    dashboardError,
    kolPoolError,
    eventsHierarchy,
    eventsGeoCount,
    eventMapRequest,
    dealersHierarchy,
    dealersGeoCount,
    dealerMapRequest,
  ]);
  const mapSelection = useMemo(
    () => resolveDashboardMapSelection(viewMode, runtimeViewModes),
    [viewMode, runtimeViewModes],
  );
  const effectiveViewMode = mapSelection.mode;
  const currentMode = effectiveViewMode ? (runtimeViewModes as any)[effectiveViewMode] : null;
  const isAvailable = currentMode?.available;
  const hierarchy = currentMode?.hierarchy || {};

  // Persist an automatic fallback only after source readiness is known. A valid user choice is retained.
  useEffect(() => {
    if (mapSelection.pending || effectiveViewMode === viewMode) return;
    setViewMode(effectiveViewMode);
    setCountry("");
    setCity("");
    setItem("");
    setVenue("");
  }, [effectiveViewMode, mapSelection.pending, viewMode]);

  // Country options
  const countryOptions = useMemo(
    () => buildCountryOptions({ currentMode, hierarchy, viewMode: effectiveViewMode || "" }),
    [currentMode, hierarchy, effectiveViewMode],
  );

  // City options
  const cityOptions = useMemo(
    () => buildCityOptions({ country, hierarchy, viewMode: effectiveViewMode || "" }),
    [country, hierarchy, effectiveViewMode],
  );

  // Item options(KOL / Store)
  const itemOptions = useMemo(() => buildItemOptions({ city, country, hierarchy }), [city, country, hierarchy]);

  // V4.5: Venue options(街道级 / 店内楼层 / Landmark)
  const venueOptions = useMemo(() => buildVenueOptions({ item, city, country, hierarchy }), [item, city, country, hierarchy]);

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
    () => buildTopListData({ currentMode, country, city, item, hierarchy, viewMode: effectiveViewMode || "" }),
    [currentMode, country, city, item, hierarchy, effectiveViewMode],
  );

  // 只读 upcoming/进行中 Events。后端与前端复用同一显式 UTC as_of_date。
  useEffect(() => {
    if (!apiToken) {
      setEventRows([]);
      setEventMapRequest({ loading: false, error: "" });
      return;
    }
    let cancelled = false;
    setEventMapRequest({ loading: true, error: "" });
    listUpcomingEvents(apiToken, 200, eventAsOfDate)
      .then((res) => {
        if (cancelled) return;
        setEventRows(Array.isArray(res?.items) ? res.items : []);
        setEventMapRequest({ loading: false, error: "" });
      })
      .catch((error) => {
        if (!cancelled) {
          setEventRows([]);
          setEventMapRequest({
            loading: false,
            error: error instanceof Error ? error.message : "Events map API 加载失败",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, eventAsOfDate]);

  const dealerBoardOwnsLocations = activeNav === "dealers";

  // 经销商位置(主页地球 dealers 层):拉 /dealers/locations 扁平 pin;失败/无 token 静默置空。
  useEffect(() => {
    if (!apiToken) {
      setDealerPins([]);
      setDealerMapRequest({ loading: false, error: "" });
      return;
    }
    // Dealers 页自己按模块装载 locations，此时外壳再拉一次只会放大首屏并挤占 DB 连接。
    // 离开 Dealers 后 effect 会重跑，再为 Dashboard 地球层补数。
    if (dealerBoardOwnsLocations) {
      setDealerMapRequest({ loading: false, error: "" });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setDealerMapRequest({ loading: true, error: "" });
    // Defer the network start one microtask: React StrictMode performs setup -> cleanup ->
    // setup synchronously, so the discarded setup is cancelled before it can hit the API.
    // A real navigation/unmount after the request starts is still aborted by the controller.
    Promise.resolve()
      .then(() => (cancelled ? null : getDealerLocations(apiToken, { signal: controller.signal })))
      .then((res) => {
        if (!cancelled && res) {
          setDealerPins(Array.isArray(res?.pins) ? res.pins : []);
          setDealerMapRequest({ loading: false, error: "" });
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setDealerPins([]);
          setDealerMapRequest({
            loading: false,
            error: error instanceof Error ? error.message : "Dealers map API 加载失败",
          });
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [apiToken, dealerBoardOwnsLocations]);

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
        // Safari can leave an AnimatePresence exit mounted forever around lazy
        // boards (topbar changes while the previous page remains). Route stages
        // therefore replace by key immediately; only the incoming 180ms motion
        // remains, so navigation cannot be held hostage by an exit callback.
        e(m.div, {
            key: activeNav,
            className: `vkpi-page-stage vkpi-page-stage--${activeNav} min-h-[calc(100vh-4rem)]`,
            initial: { opacity: 0, y: 14 },
            animate: { opacity: 1, y: 0 },
            transition: { duration: 0.18, ease: [0.16, 1, 0.3, 1] },
          },
            activeNav === "kol-pool" && e(LazyErrorBoundary, { name: "KolPool" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "KOL Pool 加载中...")
              },
                e(KOLPoolPage as React.ComponentType<any>, {
                  items: kolPoolRows,
                  loading: kolPoolLoading,
                  error: kolPoolError,
                  apiToken,
                  staff: uiStaff,
                })
              )
            ),

            activeNav === "my-kol" && e(LazyErrorBoundary, { name: "MY KOL" },
              e(React.Suspense, {
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
              )
            ),

            activeNav === "projects" && e(LazyErrorBoundary, { name: "Projects" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Projects 加载中...")
              },
                e(LegacyProjectsPage as React.ComponentType<any>, {
                  data: dashboardData,
                  filteredProjects: dashboardData.projects || [],
                  selectedProjectId: selectedLegacyProject?.id,
                  selectedProject: selectedLegacyProject || (dashboardData.projects || [])[0],
                  openProjectId: openLegacyProjectId,
                  // consume-reset(2026-07-12):消费后清空,否则同一项目二次 ⌘K/泳道点开时
                  // openProjectId 不变 → 页内 effect 不re-run = 死点击(Events 管道 onConsumeInitialEvent 同款)。
                  onConsumeOpenProject: () => setOpenLegacyProjectId(""),
                  viewMode: appViewMode,
                  apiToken,
                  onSelectProject: setSelectedLegacyProject,
                  // 2026-06-12 波5 R3:此前为空函数死点击;最小接真 → 跳 KOL Pool 页
                  onOpenKolProfile: () => {
                    saveStoredState({ activeNav: "kol-pool" });
                    setActiveNav("kol-pool");
                  },
                  // 员工档案抽屉尚未接入 Cockpit；不传空回调，避免负责人入口呈现为可点击真空操作。
                  onOpenStaffProfile: undefined,
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
              )
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

            activeNav === "shopify" && e(LazyErrorBoundary, { name: "Shopify" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Shopify 加载中...")
              },
                e(ShopifyBoardPage as React.ComponentType<any>, { apiToken })
              )
            ),
            activeNav === "dealers" && e(LazyErrorBoundary, { name: "Dealers" },
              e(React.Suspense, {
                fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "经销商地图加载中...")
              },
                e(DealerMapPage as React.ComponentType<any>, { apiToken })
              )
            ),

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
              dashboardStorageScope: currentUser?.id ? `staff-${currentUser.id}` : "staff-unresolved",
              onNavigate: setActiveNav,
              // 跨板块拉卡(task #76):palette 按板块可见权限过滤(侧栏 canViewBoard 同源)
              canViewBoard: boardPerms.canViewBoard,
              // 全盘跨页模块:源页面依赖 CockpitApp 运行态的四个板块显式透传；
              // 其余板块仍由模块自身按 apiToken 取数。不给伪造 fallback。
              crossBoardPageProps: {
                "kol-pool": {
                  items: kolPoolRows,
                  loading: kolPoolLoading,
                  error: kolPoolError,
                  staff: uiStaff,
                },
                "my-kol": {
                  viewMode: appViewMode,
                  data: dashboardData,
                  userName,
                  userRole,
                  onRefreshData,
                  onSelectPage: handleReplicaSelectPage,
                  metrics: dashboardRuntime.metrics,
                },
                projects: {
                  data: dashboardData,
                  filteredProjects: dashboardData.projects || [],
                  selectedProjectId: selectedLegacyProject?.id,
                  selectedProject: selectedLegacyProject || (dashboardData.projects || [])[0],
                  viewMode: appViewMode,
                  onSelectProject: setSelectedLegacyProject,
                  onOpenKolProfile: () => setActiveNav("kol-pool"),
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
                },
                events: {
                  userName,
                  staff: uiStaff,
                  currentUser,
                },
              },
              // P2 穿透:设置/成员与授权全屏浮层打开时,dashboard 浮卡/地图 overlay 不渲染
              showSettingsModal: showSettingsModal || showMembersAuth,
              kpiScope, setKpiScope, t, setSelectedKpi, globeContainerRef, isAvailable, pins, currentMode,
              venue, item, city, country, setPreviewEvent, handleCountryChange, handleCityChange, handleItemChange,
              setVenue, setSelectedPin, viewMode: effectiveViewMode, setViewMode, countryOptions, cityOptions, itemOptions, venueOptions,
              breadcrumb, goBack, topListData, setSelectedEvent, setSelectedSignal, setShowAllSignals, setShowAIConfirm,
              aiRegenerating, aiRegeneration: aiTodayRegeneration, onRegenerateAiToday: regenerateAiToday,
              setSelectedMover, setShowAllMovers, setSelectedProject, setShowAllProjects,
              setSelectedPublish, setShowFullCalendar, focusTarget,
              viewModes: runtimeViewModes,
              mapSelectionLoading: mapSelection.pending,
              mapSelectionError: mapSelection.error,
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
              // Upcoming Events 只接显式 as_of_date 过滤后的 upcoming/进行中记录。
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
  );
}
