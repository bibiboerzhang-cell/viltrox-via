// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, Calendar, Camera, ChevronDown, DollarSign, FileText, Globe2, HelpCircle, List, Loader2, Menu, MessageCircle, Moon, PanelLeftClose, PanelLeftOpen, PartyPopper, Search, Sun, Ticket, TrendingUp, User, Users, Video, X } from "lucide-react";
import "./styles/mockup.css";
import "../styles/vkpi-settings-dark.css";
import { KOLPoolPage } from "./KOLPoolPage";
import { ShopifyConnectPage } from "./ShopifyConnectPage";
import { DashboardReplicaPage } from "./DashboardReplicaPage";
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
import { ReportPanel } from "./components/ReportPanel";
import { SignalsAlertsCard } from "./components/SignalsAlertsCard";
import { TaskProgressBoard } from "./components/TaskProgressBoard";
import { TopMoversCard } from "./components/TopMoversCard";
import { UpcomingEventsCard } from "./components/UpcomingEventsCard";
import { AIDecisionConfirmModal } from "./components/modals/AIDecisionConfirmModal";
import { AllMoversModal } from "./components/modals/AllMoversModal";
import { AllNotificationsModal } from "./components/modals/AllNotificationsModal";
import { AllProjectsModal } from "./components/modals/AllProjectsModal";
import { AllRemindersModal } from "./components/modals/AllRemindersModal";
import { EditGroupModal } from "./components/modals/EditGroupModal";
import { EventDetailModal } from "./components/modals/EventDetailModal";
import { EventPreviewModal } from "./components/modals/EventPreviewModal";
import { FeedbackModal } from "./components/modals/FeedbackModal";
import { FullCalendarModal } from "./components/modals/FullCalendarModal";
import { KOLDetailModal } from "./components/modals/KOLDetailModal";
import { KPIDetailModal } from "./components/modals/KPIDetailModal";
import { NotificationDetailModal } from "./components/modals/NotificationDetailModal";
import { PinDetailModal } from "./components/modals/PinDetailModal";
import { ProfileModal } from "./components/modals/ProfileModal";
import { ProjectDetailModal } from "./components/modals/ProjectDetailModal";
import { PublishPreviewModal } from "./components/modals/PublishPreviewModal";
import { SettingsPage } from "../pages/SettingsPage";
import { ShortcutsModal } from "./components/modals/ShortcutsModal";
import { SignalDetailModal } from "./components/modals/SignalDetailModal";
import { SignalsAllModal } from "./components/modals/SignalsAllModal";
import { TeamModal } from "./components/modals/TeamModal";
import { HelpPopover } from "./components/popovers/HelpPopover";
import { NotificationsPopover } from "./components/popovers/NotificationsPopover";
import { UserMenuPopover } from "./components/popovers/UserMenuPopover";
import { WorkRemindersPopover } from "./components/popovers/WorkRemindersPopover";
import { logoutV615, resolveV615Alert } from "./api";
import { buildApiUrl } from "../../../services/http";
import { listEvents } from "../../../services/vkpi/events-api";
import { normalizeEventsHierarchy } from "./normalizers";
import { I18nContext, makeT } from "./lib/i18n";
import { loadStoredState, saveStoredState } from "./lib/storage";
import { useV615Runtime } from "./useV615Runtime";
import { createProject, deleteProject, updateProject } from "../../../services/vkpi/projects-api";
import { addProjectCost } from "../../../services/vkpi/cost-api";
import { toUiStaffList } from "../../../services/vkpi/staffAdapter";
import { listStaffGroups, createStaffGroup, updateStaffGroup, toUiGroup } from "../../../services/vkpi/groups-api";
import { KPI_SCOPES } from "./data/kpiScopes";
import { NAV_ITEMS } from "./data/navItems";
import { VIEW_MODES } from "./data/viewModes";
import { emptyDashboardData } from "../data/emptyDashboardData";

const e = React.createElement;
const MyKolPage = React.lazy(() => import("../pages/myKol/MyKolPage").then((module) => ({ default: module.MyKolPage })));
const LegacyProjectsPage = React.lazy(() => import("../pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })));
const EventsMockupPage = React.lazy(() => import("../pages/events/EventsMockupPage").then((module) => ({ default: module.EventsMockupPage })));

export function V615ReplicaApp(props: any = {}) {
  const {
    apiToken,
    userName,
    userRole,
    userAvatar,
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
  const urlNav = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("v615") : "";
  const urlReport = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("report") : "";
  const normalizeReplicaNav = (key) => key === "discover" || key === "channels" ? "my-kol" : key;
  const initialNav = normalizeReplicaNav(urlNav) || normalizeReplicaNav(stored.activeNav) || "dashboard";
  
  const [collapsed, setCollapsed] = useState(stored.collapsed || false);
  const [activeNav, setActiveNav] = useState(["dashboard", "kol-pool", "my-kol", "projects", "events", "shopify"].includes(initialNav) ? initialNav : "dashboard");
  const [theme, setTheme] = useState(stored.theme || "dark");

  // 版本徽标:启动时拉一次 /health,展示 server 短 sha 与前后端同步状态(纯只读,失败静默)
  const [versionBadge, setVersionBadge] = useState(null);
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

  useEffect(() => {
    const handleOpenKolSearchSession = () => {
      setActiveNav("kol-pool");
    };
    // 从哪发起回哪去(2026-06-12):账号分析任务点开切回 MY KOL(行定位由 MyKolPage 自取 pending key)
    const handleOpenMyKolKol = () => {
      setActiveNav("my-kol");
    };
    // 2026-06-12 波5 R5:泳道 target_type=project 的任务点开 → 直达项目详情(复用 Active Campaigns 同款管道)
    const handleOpenProjectTask = (event) => {
      const projectId = String(event?.detail?.projectId || "");
      if (projectId) setOpenLegacyProjectId(projectId);
      setActiveNav("projects");
    };
    window.addEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
    window.addEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
    window.addEventListener("vkpi:open-project-task", handleOpenProjectTask);
    return () => {
      window.removeEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
      window.removeEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
      window.removeEventListener("vkpi:open-project-task", handleOpenProjectTask);
    };
  }, []);
  
  // 层级 state
  const [viewMode, setViewMode]   = useState(stored.viewMode || null);
  const [country, setCountry]     = useState(stored.country || "");
  const [city, setCity]           = useState(stored.city || "");
  const [item, setItem]           = useState(stored.item || "");
  const [venue, setVenue]         = useState(stored.venue || "");

  const [selectedPin, setSelectedPin] = useState(null);
  const [selectedLegacyProject, setSelectedLegacyProject] = useState(null);
  const [openLegacyProjectId, setOpenLegacyProjectId] = useState("");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [selectedKpi, setSelectedKpi] = useState(null);
  const [previewEvent, setPreviewEvent] = useState(null);
  // 2026-06-14 诚实化:Upcoming Events 卡接真实 /api/admin/vkpi/events,不再传空数组。
  const [eventRows, setEventRows] = useState([]);
  const [kpiScope, setKpiScope] = useState(stored.kpiScope || "all");
  const [reportOpen, setReportOpen] = useState(urlReport === "1"); // V6.10: Report Panel
  const [selectedSignal, setSelectedSignal] = useState(null); // V6.11: Signal detail modal
  // V6.13: 新 modal states
  const [selectedProject, setSelectedProject] = useState(null); // Active Campaigns 项目详情
  const [selectedPublish, setSelectedPublish] = useState(null); // 7 天日历某条
  const [selectedMover, setSelectedMover] = useState(null);     // Top Movers 单条
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
  const [viewingAs, setViewingAs] = useState(null);  // Admin 切换查看身份
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
  } = useV615Runtime({ apiToken, userName, userRole, userAvatar, starredProjects: dashboardData.starredProjects || [] });
  const activeStaffId = viewingAs ? viewingAs.id : currentUser.id;
  const activeReminders = useMemo(() => viewingAs ? [] : runtimeReminders, [viewingAs, runtimeReminders]);
  // Real staff (17) adapted to the UI shape the team/group/events modals expect.
  const uiStaff = useMemo(() => toUiStaffList(dashboardData.staffMembers || []), [dashboardData.staffMembers]);
  // Real staff-groups loaded from the new backend (replaces hardcoded "KOL Operations").
  const [staffGroups, setStaffGroups] = useState([]);
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
  const [editGroupTarget, setEditGroupTarget] = useState(null);
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
  const pushLocalNotification = (notification) => {
    setRuntimeNotifications(prev => [notification, ...prev].slice(0, 80));
  };
  const handleFeedbackSubmitted = (result, payload) => {
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
  const handleReplicaSelectPage = (page) => {
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
    if (page === "v615Replica" || page === "dashboardPremium") {
      setActiveNav("dashboard");
      return;
    }
    onSelectPage?.(page);
  };
  const refreshReplicaProjects = useCallback(async () => {
    await onRefreshData?.();
  }, [onRefreshData]);
  const handleReplicaCreateProject = useCallback(async (payload) => {
    if (!apiToken) throw new Error("缺少 API token，不能创建项目。");
    const visiblePayload = payload?.sourceType === "v615_projects_ui"
      ? { ...payload, sourceType: "excel_promo_plan" }
      : payload;
    const result = await createProject(apiToken, visiblePayload);
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaUpdateProject = useCallback(async (projectId, payload) => {
    if (!apiToken) throw new Error("缺少 API token，不能更新项目。");
    const result = await updateProject(apiToken, projectId, payload);
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaDeleteProject = useCallback(async (projectId, reason) => {
    if (!apiToken) throw new Error("缺少 API token，不能取消项目。");
    const result = await deleteProject(apiToken, projectId, reason || "用户在 V615 Projects 取消项目");
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  const handleReplicaAddProjectCost = useCallback(async (payload) => {
    if (!apiToken) throw new Error("缺少 API token，不能登记费用。");
    const result = await addProjectCost(apiToken, payload);
    await refreshReplicaProjects();
    return result;
  }, [apiToken, refreshReplicaProjects]);
  // 7 子 modals
  const [showProfile, setShowProfile] = useState(false);
  const [showTeam, setShowTeam] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);
  // V6.14.4: ViewAll modals + NotifDetail + EditGroup
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [showAllMovers, setShowAllMovers] = useState(false);
  const [showFullCalendar, setShowFullCalendar] = useState(false);
  const [showAllReminders, setShowAllReminders] = useState(false);
  const [showAllNotifs, setShowAllNotifs] = useState(false);
  const [selectedNotif, setSelectedNotif] = useState(null);
  const [showEditGroup, setShowEditGroup] = useState(false);
  const [editGroupMode, setEditGroupMode] = useState("edit");
  const [editGroupName, setEditGroupName] = useState("KOL Operations");
  const globeContainerRef = useRef(null);

  useEffect(() => {
    document.body.classList.toggle("light", theme === "light");
  }, [theme]);
  
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
      desc: "Dealer locations endpoint 待接入",
      hierarchy: {},
      available: false,
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
  }), [dashboardRuntime.mapHierarchy, kolHierarchyReady, eventsHierarchy, eventsGeoCount]);
  const currentMode = viewMode ? runtimeViewModes[viewMode] : null;
  const isAvailable = currentMode?.available;
  const hierarchy = currentMode?.hierarchy || {};

  // Country options
  const countryOptions = useMemo(() => {
    if (!currentMode) return [];
    return [
      { key: "", label: "All Countries", sub: "World view" },
      ...Object.entries(hierarchy)
        .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,M]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,M]/g, "")) || 0))
        .map(([name, data]) => ({
          key: name,
          label: name,
          sub: viewMode === "kols" ? `${data.count} KOLs` : `Revenue ${data.revenue}`,
          badge: viewMode === "kols" ? data.count : data.revenue,
        }))
    ];
  }, [currentMode, hierarchy, viewMode]);

  // City options
  const cityOptions = useMemo(() => {
    if (!country || !hierarchy[country]?.cities) return [];
    return [
      { key: "", label: `All of ${country}`, sub: "Country view" },
      ...Object.entries(hierarchy[country].cities)
        .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,]/g, "")) || 0))
        .map(([name, data]) => ({
          key: name,
          label: name,
          sub: viewMode === "kols" ? `${data.count} KOLs` : `Revenue ${data.revenue}`,
          badge: viewMode === "kols" ? data.count : data.revenue,
        }))
    ];
  }, [country, hierarchy, viewMode]);

  // Item options(KOL / Store)
  const itemOptions = useMemo(() => {
    if (!city || !hierarchy[country]?.cities[city]) return [];
    const cityData = hierarchy[country].cities[city];
    const items = cityData.kols || cityData.stores || [];
    return [
      { key: "", label: `All in ${city}`, sub: "City view" },
      ...items.map((d) => ({
        key: d.handle || d.name,
        label: d.handle || d.name,
        sub: d.niche || d.type,
        badge: d.engagement || d.revenue,
      }))
    ];
  }, [city, country, hierarchy]);

  // V4.5: Venue options(街道级 / 店内楼层 / Landmark)
  const venueOptions = useMemo(() => {
    if (!item || !city || !hierarchy[country]?.cities[city]) return [];
    const cityData = hierarchy[country].cities[city];
    const items = cityData.kols || cityData.stores || [];
    const sel = items.find((d) => (d.handle || d.name) === item);
    if (!sel || !sel.venues || sel.venues.length === 0) return [];
    return [
      { key: "", label: `All venues`, sub: `${sel.handle || sel.name} all locations` },
      ...sel.venues.map((v) => ({
        key: v.name,
        label: v.name,
        sub: v.note || v.type,
        badge: v.type,
      }))
    ];
  }, [item, city, country, hierarchy]);

  // 计算当前要在地球上显示的 pins
  const pins = useMemo(() => {
    if (!currentMode || !isAvailable) return [];

    // Events endpoint is not wired yet; do not render prototype event pins as real data.
    if (currentMode.isEvents) {
      return [];
    }

    // Level 0:World view — 所有国家
    if (!country) {
      return Object.entries(hierarchy).map(([name, data]) => ({
        name, lat: data.lat, lng: data.lng,
        count: data.count, revenue: data.revenue,
        color: currentMode.pinColor,
        scale: 1,
      }));
    }

    // Level 1:Country view — 该国所有城市
    if (!city && hierarchy[country]?.cities) {
      return Object.entries(hierarchy[country].cities).map(([cityName, cityData]) => ({
        name: cityName,
        country, lat: cityData.lat, lng: cityData.lng,
        count: cityData.count, revenue: cityData.revenue,
        color: currentMode.pinColor,
        scale: 0.75,
      }));
    }

    // Level 2:City view — 该城市具体 KOL / Store
    if (!item && hierarchy[country]?.cities[city]) {
      const cityData = hierarchy[country].cities[city];
      const items = cityData.kols || cityData.stores || [];
      return items.map((d) => ({
        ...d, country, city,
        color: currentMode.pinColor,
        scale: 0.6,
      }));
    }

    // Level 3:Item view — 选了具体 KOL/Store
    if (item && hierarchy[country]?.cities[city]) {
      const cityData = hierarchy[country].cities[city];
      const items = cityData.kols || cityData.stores || [];
      const sel = items.find((d) => (d.handle || d.name) === item);
      if (!sel) return [];

      // 如果没选 venue 但 item 有 venues,显示所有 venues
      if (!venue && sel.venues && sel.venues.length > 0) {
        return [
          { ...sel, country, city, color: currentMode.pinColor, scale: 1.0, isParent: true },
          ...sel.venues.map((v) => ({
            ...v, country, city, parentItem: sel.handle || sel.name,
            color: currentMode.pinColor, scale: 0.5,
          }))
        ];
      }

      // Level 4:Venue 选中 — 单个高亮
      if (venue && sel.venues) {
        const v = sel.venues.find((x) => x.name === venue);
        if (v) return [{ ...v, country, city, parentItem: sel.handle || sel.name, color: currentMode.pinColor, scale: 1.4 }];
      }

      // 没 venues,就显示 item 自己
      return [{ ...sel, country, city, color: currentMode.pinColor, scale: 1.3 }];
    }

    return [];
  }, [currentMode, isAvailable, country, city, item, venue, hierarchy]);

  // 计算 focusTarget
  const focusTarget = useMemo(() => {
    if (!currentMode) return null;
    // V6.9.2: Events viewing — 根据 country/city 飞
    if (currentMode.isEvents) {
      if (city && hierarchy[country]?.cities[city]) {
        const c = hierarchy[country].cities[city];
        return { lat: c.lat, lng: c.lng, zoom: 11 };
      }
      if (country && hierarchy[country]) {
        return { lat: hierarchy[country].lat, lng: hierarchy[country].lng, zoom: 4 };
      }
      return { lat: 30, lng: 0, zoom: 2 };
    }
    // V4.7: 选了 country 立刻切 2D 地图,focusTarget 用 Leaflet zoom 体系
    // venue: 街景级
    if (venue && hierarchy[country]?.cities[city]) {
      const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
      const sel = items.find((d) => (d.handle || d.name) === item);
      const v = sel?.venues?.find((x) => x.name === venue);
      if (v) return { lat: v.lat, lng: v.lng, zoom: 18 };
    }
    // item: 街区级
    if (item && hierarchy[country]?.cities[city]) {
      const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
      const sel = items.find((d) => (d.handle || d.name) === item);
      if (sel) return { lat: sel.lat, lng: sel.lng, zoom: 16 };
    }
    // city: 城市级
    if (city && hierarchy[country]?.cities[city]) {
      const c = hierarchy[country].cities[city];
      return { lat: c.lat, lng: c.lng, zoom: 13 };
    }
    // country: 国家级
    if (country && hierarchy[country]) {
      const c = hierarchy[country];
      return { lat: c.lat, lng: c.lng, zoom: 5 };
    }
    // V6.1: World 视图
    return { lat: 30, lng: 0, zoom: 2 };
  }, [country, city, item, venue, hierarchy, currentMode]);

  // 面包屑路径
  const breadcrumb = useMemo(() => {
    const arr = [];
    if (country) arr.push(country);
    if (city) arr.push(city);
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
  const handleCountryChange = (c) => {
    setCountry(c); setCity(""); setItem(""); setVenue("");
  };
  const handleCityChange = (c) => {
    setCity(c); setItem(""); setVenue("");
  };
  const handleItemChange = (i) => {
    setItem(i); setVenue("");
  };

  // Top List 数据(根据当前层级动态生成)
  const topListData = useMemo(() => {
    if (!currentMode) return { title: "", items: [] };
    if (!country) {
      const total = viewMode === "kols"
        ? Math.max(1, Object.values(hierarchy).reduce((sum, item) => sum + (Number(item.count) || 0), 0))
        : 100;
      return {
        title: viewMode === "kols" ? "Top Regions by KOL Count" : "Top Regions by Revenue",
        items: Object.entries(hierarchy)
          .slice()
          .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,M]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,M]/g, "")) || 0))
          .slice(0, 7)
          .map(([name, d]) => ({
            label: name,
            value: viewMode === "kols" ? `${Number(d.count || 0).toLocaleString()} KOLs` : d.revenue,
            barWidth: viewMode === "kols" ? Math.max(4, Math.min(100, (Number(d.count || 0) / total) * 100)) : Math.max(20, 100 - 0),
          }))
      };
    }
    if (!city && hierarchy[country]?.cities) {
      return {
        title: `Top Cities in ${country}`,
        items: Object.entries(hierarchy[country].cities)
          .slice()
          .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,]/g, "")) || 0))
          .map(([name, d]) => ({
            label: name,
            value: viewMode === "kols" ? `${d.count} KOLs` : d.revenue,
            barWidth: Math.max(6, Math.min(100, (Number(d.count || 0) / Math.max(1, Number(hierarchy[country].count || 0))) * 100)),
          }))
      };
    }
    if (city && hierarchy[country]?.cities[city] && !item) {
      const cityData = hierarchy[country].cities[city];
      const items = cityData.kols || cityData.stores || [];
      return {
        title: viewMode === "kols" ? `KOLs in ${city}` : `Dealers in ${city}`,
        // 2026-06-14 诚实化:此层无 per-KOL 真实排序量级,改用统一占位条宽(不再 Math.random 伪造排名)。
        items: items.map((d) => ({
          label: d.handle || d.name,
          value: d.engagement || d.revenue,
          barWidth: 60,
        }))
      };
    }
    // V4.5: Venue level — 显示该 KOL/Store 的所有 venue 地点
    if (item && hierarchy[country]?.cities[city]) {
      const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
      const sel = items.find((d) => (d.handle || d.name) === item);
      if (sel && sel.venues && sel.venues.length > 0) {
        return {
          title: `${sel.handle || sel.name} · Locations`,
          items: sel.venues.map((v) => ({
            label: v.name,
            value: v.type,
            barWidth: 80,
          }))
        };
      }
    }
    return { title: "", items: [] };
  }, [currentMode, country, city, item, hierarchy, viewMode]);

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

  // 真实 events → UpcomingEventsCard 形态:start_date >= 今天,升序,取前 6。
  const upcomingEvents = useMemo(() => {
    const EVENT_TYPE_VISUAL = {
      tradeshow: { icon: Ticket, color: "#a855f7" },
      media: { icon: Camera, color: "#06b6d4" },
      webinar: { icon: Video, color: "#10b981" },
      kol_meetup: { icon: PartyPopper, color: "#fbbf24" },
      internal: { icon: Users, color: "#94a3b8" },
    };
    // 时区安全:用本地 YYYY-MM-DD 字符串比较(Date.parse 把 date-only 当 UTC,
    // 在 UTC- 时区会把"今天"的活动算成昨天而漏掉 → 之前 Upcoming(0) 的真因)。
    const nowLocal = new Date();
    const todayStr = `${nowLocal.getFullYear()}-${String(nowLocal.getMonth() + 1).padStart(2, "0")}-${String(nowLocal.getDate()).padStart(2, "0")}`;
    return (Array.isArray(eventRows) ? eventRows : [])
      .filter((row) => String(row?.start_date || "").slice(0, 10) >= todayStr)
      .sort((a, b) => String(a.start_date || "").localeCompare(String(b.start_date || "")))
      .slice(0, 6)
      .map((row) => {
        const visual = EVENT_TYPE_VISUAL[row.type_key] || { icon: Calendar, color: "#a855f7" };
        const start = new Date(String(row.start_date));
        const dateLabel = Number.isNaN(start.getTime())
          ? String(row.start_date || "").slice(0, 10)
          : start.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
        const locParts = [row.location_city, row.location_country].filter(Boolean);
        const health = Number(row.health_score);
        return {
          id: row.id,
          title: String(row.title || "未命名活动"),
          date: dateLabel,
          location: String(row.location_name || locParts.join(" · ") || "地点待补"),
          country: String(row.location_country || ""),
          city: String(row.location_city || ""),
          icon: visual.icon,
          color: visual.color,
          // 真实 health_score 作为进度条;无则 0(不编造)。
          materialProgress: Number.isFinite(health) ? Math.max(0, Math.min(100, health)) : 0,
          raw: row,
        };
      });
  }, [eventRows]);

  return e(I18nContext.Provider, { value: { t, lang, setLang } },
   e("div", { className: "relative min-h-screen bg-[#050810] text-slate-200" },
    e("div", {
      className: "pointer-events-none fixed inset-0",
      style: { background: "radial-gradient(circle at 30% -10%,rgba(65,73,255,.22),transparent 35%),radial-gradient(circle at 90% 20%,rgba(0,213,200,.08),transparent 30%),linear-gradient(180deg,#060a15,#03060c)" }
    }),

    e(AnimatePresence, null, selectedPin && e(PinDetailModal, {
      pin: selectedPin,
      mode: currentMode,
      onClose: () => setSelectedPin(null),
      // 2026-06-12 死按钮诚实化:View full profile → 跳 KOL Pool 真页
      onOpenKolPool: () => {
        setSelectedPin(null);
        saveStoredState({ activeNav: "kol-pool" });
        setActiveNav("kol-pool");
      },
    })),
    e(AnimatePresence, null, selectedEvent && e(EventDetailModal, { event: selectedEvent, onClose: () => setSelectedEvent(null) })),
    e(AnimatePresence, null, selectedKpi && e(KPIDetailModal, {
      kpiId: selectedKpi,
      initialScope: kpiScope,
      metrics: dashboardRuntime.metrics,
      onClose: () => setSelectedKpi(null),
      onDrillToKolPool: () => {
        saveStoredState({ activeNav: "kol-pool" });
        setSelectedKpi(null);
        setActiveNav("kol-pool");
      },
    })),
    // V6.10: Report Panel
    e(AnimatePresence, null, reportOpen && e(ReportPanel, { onClose: () => setReportOpen(false), data: reportData })),
    // V6.11: Signal Detail Modal
    e(AnimatePresence, null, selectedSignal && e(SignalDetailModal, { alert: selectedSignal, onClose: () => setSelectedSignal(null) })),
    // V6.13: 新 modal mounts
    e(AnimatePresence, null, selectedProject && e(ProjectDetailModal, {
      project: selectedProject,
      staff: uiStaff,
      apiToken,
      onAssigned: () => { onRefreshData && onRefreshData(); },
      onClose: () => setSelectedProject(null),
      onOpenFullPage: (project) => {
        const projectId = String(project?.projectId || project?.id || "");
        const row = (dashboardData.projects || []).find((item) => item.id === projectId);
        setSelectedLegacyProject(row || null);
        setOpenLegacyProjectId(projectId);
        setSelectedProject(null);
        saveStoredState({ activeNav: "projects" });
        setActiveNav("projects");
      },
    })),
    e(AnimatePresence, null, selectedPublish && e(PublishPreviewModal, { item: selectedPublish, onClose: () => setSelectedPublish(null) })),
    e(AnimatePresence, null, selectedMover && e(KOLDetailModal, {
      mover: selectedMover,
      onClose: () => setSelectedMover(null),
      // 2026-06-12 死按钮诚实化:查看完整档案 → 跳 KOL Pool 真页
      onOpenKolPool: () => {
        setSelectedMover(null);
        saveStoredState({ activeNav: "kol-pool" });
        setActiveNav("kol-pool");
      },
    })),
    e(AnimatePresence, null, showAllSignals && e(SignalsAllModal, { 
      alerts: dashboardRuntime.signals, 
      onClose: () => setShowAllSignals(false), 
      onAlertClick: (a) => { setShowAllSignals(false); setSelectedSignal(a); }
    })),
    e(AnimatePresence, null, showAIConfirm && e(AIDecisionConfirmModal, {
      insight: dashboardRuntime.aiInsight,
      onClose: () => setShowAIConfirm(false),
      // 2026-06-12 波5 R8:alert 假动作换成本地通知,明示未写入后端
      onConfirm: () => {
        setShowAIConfirm(false);
        pushLocalNotification({
          id: `ai-confirm-${Date.now()}`,
          raw: {},
          iconKey: "bell",
          iconColor: "#a855f7",
          title: "AI 建议确认仅记录在本地",
          desc: "真实任务创建接口待接入,本次确认未写入后端",
          time: "刚刚",
          unread: true,
          category: "notification",
          severity: "medium",
          status: "todo",
          priority: "medium",
          source: "ai_confirm_local",
        });
      }
    })),
    // V6.14.2: 顶部 4 按钮 popovers - 锚定到具体按钮 + i18n + role
    e(AnimatePresence, null, showHelp && e(HelpPopover, { 
      onClose: () => setShowHelp(false), 
      anchorRef: helpBtnRef, t,
      onOpenDocs: null,
      onOpenShortcuts: () => setShowShortcuts(true),
      onOpenFeedback: () => setShowFeedback(true),
    })),
    e(AnimatePresence, null, showMessages && e(WorkRemindersPopover, { 
      onClose: () => setShowMessages(false), 
      reminders: activeReminders,
      anchorRef: messagesBtnRef, t, viewingAs,
      onViewAll: () => setShowAllReminders(true),
    })),
    e(AnimatePresence, null, showNotifs && e(NotificationsPopover, { 
      onClose: () => setShowNotifs(false), 
      notifications: runtimeNotifications, 
      anchorRef: notifsBtnRef, t,
      onViewAll: () => setShowAllNotifs(true),
      onItemClick: (n) => setSelectedNotif(n),
    })),
    e(AnimatePresence, null, showUserMenu && e(UserMenuPopover, { 
      onClose: () => setShowUserMenu(false), 
      theme, onToggleTheme: () => setTheme(t => t === "light" ? "dark" : "light"), 
      anchorRef: userMenuBtnRef, t, user: currentUser, staff: uiStaff, lang,
      onToggleLang: () => setLang(l => l === "zh" ? "en" : "zh"),
      viewingAs, onResetView: () => setViewingAs(null),
      onOpenProfile: () => setShowProfile(true),
      onOpenTeam: () => setShowTeam(true),
      onOpenSettings: () => setShowSettingsModal(true),
      onImpersonate: (s) => setViewingAs(s),
      onLogout: async () => {
        await logoutV615().catch(() => null);
        onSignOut && onSignOut();
      },
    })),
    // V6.14.2: 7 子 modals
    e(AnimatePresence, null, showProfile && e(ProfileModal, { user: currentUser, onClose: () => setShowProfile(false), t })),
    e(AnimatePresence, null, showTeam && e(TeamModal, {
      user: currentUser, staff: uiStaff, groups: staffGroups,
      onClose: () => setShowTeam(false),
      onImpersonate: (s) => setViewingAs(s),
      onOpenEditGroup: (g) => openGroupEditor("edit", g || null),
      onOpenNewGroup: () => openGroupEditor("new"),
      t
    })),
    showSettingsModal && e("div", { className: "v615-settings-dark fixed inset-0 z-[1000] bg-[#0a0a0d] overflow-auto" },
      e("button", {
        onClick: () => setShowSettingsModal(false),
        className: "fixed top-4 right-5 z-[210] rounded-md border border-white/10 bg-white/5 p-2 text-slate-300 hover:text-white hover:bg-white/10",
        title: t("关闭"),
      }, e(X, { size: 18 })),
      e(SettingsPage, {
        data: dashboardData,
        viewMode: appViewMode === "employee" ? "employee" : "manager",
        apiToken,
        onRefreshData,
      })
    ),
    e(AnimatePresence, null, showShortcuts && e(ShortcutsModal, { onClose: () => setShowShortcuts(false) })),
    e(AnimatePresence, null, showFeedback && e(FeedbackModal, { onClose: () => setShowFeedback(false), apiToken, onSubmitted: handleFeedbackSubmitted })),
    // V6.14.4: ViewAll modals + NotifDetail + EditGroup
    e(AnimatePresence, null, showAllProjects && e(AllProjectsModal, { 
      campaigns: dashboardRuntime.campaigns, 
      onClose: () => setShowAllProjects(false),
      onProjectClick: (c) => setSelectedProject(c)
    })),
    e(AnimatePresence, null, showAllMovers && e(AllMoversModal, { 
      movers: dashboardRuntime.topMovers, 
      onClose: () => setShowAllMovers(false),
      onMoverClick: (m) => setSelectedMover(m)
    })),
    e(AnimatePresence, null, showFullCalendar && e(FullCalendarModal, { 
      days: dashboardRuntime.calendarDays, 
      onClose: () => setShowFullCalendar(false),
      onItemClick: (item) => setSelectedPublish(item)
    })),
    e(AnimatePresence, null, showAllReminders && e(AllRemindersModal, { 
      reminders: activeReminders, 
      onClose: () => setShowAllReminders(false),
      viewingAs
    })),
    e(AnimatePresence, null, showAllNotifs && e(AllNotificationsModal, { 
      notifications: runtimeNotifications, 
      onClose: () => setShowAllNotifs(false),
      onNotifClick: (n) => setSelectedNotif(n)
    })),
    e(AnimatePresence, null, selectedNotif && e(NotificationDetailModal, { 
      notification: selectedNotif, 
      onClose: () => setSelectedNotif(null),
      onMarkRead: async (id) => {
        if (!apiToken) return;
        await resolveV615Alert(apiToken, id).catch(() => null);
        setRuntimeNotifications(prev => prev.map(item => item.id === id ? { ...item, unread: false, status: "done" } : item));
      }
    })),
    e(AnimatePresence, null, showEditGroup && e(EditGroupModal, {
      groupName: editGroupName,
      mode: editGroupMode,
      staff: uiStaff,
      initialMembers: editGroupTarget?.member_ids || [],
      initialDesc: editGroupTarget?.description || "",
      permissions: editGroupTarget?.permissions || null,
      onClose: () => setShowEditGroup(false),
      onSave: async (group) => {
        try {
          if (!apiToken) throw new Error("缺少 API token，不能保存分组。");
          const body = { name: group.name, description: group.desc, member_ids: group.members, permissions: group.permissions };
          if (group.mode === "new") {
            await createStaffGroup(apiToken, body);
          } else if (editGroupTarget?.id) {
            await updateStaffGroup(apiToken, editGroupTarget.id, body);
          }
          await refreshStaffGroups();
          pushLocalNotification({
            id: `team-group-${Date.now()}`,
            raw: group,
            iconKey: "bell",
            iconColor: "#a855f7",
            title: group.mode === "new" ? `新建分组: ${group.name}` : `分组已更新: ${group.name}`,
            desc: "已写入 staff-groups",
            time: "刚刚",
            unread: true,
            category: "notification",
            severity: "low",
            status: "done",
            priority: "low",
            source: "team_group",
          });
        } catch (err) {
          pushLocalNotification({
            id: `team-group-err-${Date.now()}`,
            raw: { error: String(err && err.message ? err.message : err) },
            iconKey: "warning",
            iconColor: "#ef4444",
            title: "分组保存失败",
            desc: String(err && err.message ? err.message : err),
            time: "刚刚",
            unread: true,
            category: "notification",
            severity: "high",
            status: "todo",
            priority: "high",
            source: "team_group_error",
          });
        }
      }
    })),
    // V6.9: Event preview modal (二次点击逻辑)
    e(AnimatePresence, null, previewEvent && e(EventPreviewModal, { 
      event: previewEvent, 
      allEvents: [],
      onClose: () => setPreviewEvent(null),
      onViewDetails: (evt) => {
        setPreviewEvent(null);
        setSelectedEvent(evt);
      }
    })),

    e("div", { className: "v615-shell relative mx-auto flex min-h-screen max-w-[1920px]" },

      // ─── Sidebar ───
      e("aside", {
        className: `${collapsed ? "w-[64px]" : "w-[260px]"} sticky top-0 hidden h-screen shrink-0 flex-col justify-between border-r border-white/[0.06] bg-[#050810]/85 backdrop-blur-xl transition-all duration-300 md:flex`
      },
        e("div", null,
          e("div", { className: `flex h-16 items-center ${collapsed ? "justify-center" : "px-5"}` },
            e("div", { className: "text-2xl font-black tracking-[.18em] text-white" }, collapsed ? "V" : "VILTROX")
          ),
          e("nav", { className: `space-y-1 ${collapsed ? "px-2" : "px-3"}` },
            NAV_ITEMS.map(({ icon: Icon, label, badge, key }) => {
              const active = activeNav === key;
              return e("button", {
                key, onClick: () => setActiveNav(key), title: collapsed ? label : undefined,
                className: `group flex w-full items-center ${collapsed ? "justify-center px-2" : "px-3 gap-3"} rounded-lg py-2 text-sm transition ${
                  active ? "bg-blue-600/20 text-white" : "text-slate-400 hover:bg-white/[0.04] hover:text-white"
                }`,
                style: active ? { boxShadow: "inset 2px 0 0 #60a5fa" } : {},
              },
                e(Icon, { size: 16, className: active ? "text-blue-300" : "text-slate-500" }),
                !collapsed && e("span", { className: "flex-1 text-left" }, label),
                !collapsed && badge && e("span", {
                  className: `rounded px-1.5 py-0.5 text-[10px] ${
                    badge === "New" ? "bg-emerald-500/30 text-emerald-200" :
                    "bg-slate-600/40 text-slate-300"
                  }`
                }, badge)
              );
            })
          )
        ),
        e("div", { className: `flex flex-col gap-2 ${collapsed ? "px-2" : "px-3"} pb-4` },
          !collapsed && e(TaskProgressBoard, { apiToken }),
          e("button", {
            onClick: () => setCollapsed(!collapsed),
            className: `flex items-center ${collapsed ? "justify-center" : "gap-3 px-3"} rounded-lg py-2 text-sm text-slate-400 hover:bg-white/[0.04] hover:text-white`,
          },
            collapsed ? e(PanelLeftOpen, { size: 16 }) : e(PanelLeftClose, { size: 16 }),
            !collapsed && e("span", null, "Collapse")
          ),
          e("button", {
            onClick: () => setTheme(theme === "dark" ? "light" : "dark"),
            className: `flex items-center ${collapsed ? "justify-center" : "gap-3 px-3"} rounded-lg py-2 text-sm text-slate-400 hover:bg-white/[0.04] hover:text-white`,
          },
            theme === "dark" ? e(Moon, { size: 16 }) : e(Sun, { size: 16 }),
            !collapsed && e("span", null, theme === "dark" ? "Dark" : "Light")
          ),
          // 版本徽标:仅展开态显示一行极淡文字,折叠态隐藏以免挤压窄轨
          !collapsed && versionBadge && versionBadge.shortSha && e("div", {
            className: "px-3 pt-1 text-[10px] leading-tight text-slate-600 select-none",
            title: versionBadge.hasClient
              ? (versionBadge.inSync ? "前后端构建一致" : "前端与后端构建不一致")
              : "前端构建标记缺失",
          },
            `${versionBadge.shortSha} · ${versionBadge.inSync ? "✓同步" : "⚠不一致"}`
          )
        )
      ),

      // ─── Main ───
      e("main", { className: "min-w-0 flex-1" },

        // Header
        e("header", { className: "sticky top-0 z-40 flex h-16 items-center justify-between border-b border-white/[0.06] bg-[#050810]/80 px-4 backdrop-blur-xl md:px-6" },
          e("div", { className: "text-sm font-semibold tracking-wide text-white" }, (NAV_ITEMS.find(n => n.key === activeNav)?.label) || "Dashboard"),
          e("div", { className: "mx-4 hidden max-w-md flex-1 md:block" },
            e("div", { className: "relative" },
              e(Search, { size: 14, className: "absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" }),
              e("input", {
                placeholder: "Search anything...",
                className: "w-full rounded-lg border border-white/[0.08] bg-white/[0.03] py-1.5 pl-9 pr-3 text-xs text-white placeholder-slate-500 focus:border-blue-500/40 focus:outline-none"
              })
            )
          ),
          e("div", { className: "flex items-center gap-2 md:gap-3" },
            // V6.14: Help & Messages 按钮(加 onClick + ref)
            e("button", {
              ref: helpBtnRef,
              onClick: () => setShowHelp(true),
              "aria-label": "Help",
              className: "hidden rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
            }, e(HelpCircle, { size: 16 })),
            e("button", {
              ref: messagesBtnRef,
              onClick: () => setShowMessages(true),
              "aria-label": "Work Reminders",
              className: "hidden relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white md:block"
            }, 
              e(MessageCircle, { size: 16 }),
              activeReminders.filter(r => r.status === "todo").length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
            ),
            // V6.10: Generate Report 按钮
            e("button", {
              onClick: () => setReportOpen(true),
              "aria-label": "Generate Report",
              className: "hidden md:flex items-center gap-1.5 rounded-lg border border-purple-500/30 bg-purple-500/[0.08] px-2.5 py-1.5 text-xs text-purple-200 hover:bg-purple-500/[0.15] hover:border-purple-500/50"
            },
              e(FileText, { size: 13 }),
              e("span", null, "Report")
            ),
            // V6.14: Notifications(加 onClick + ref)
            e("button", {
              ref: notifsBtnRef,
              onClick: () => setShowNotifs(true),
              "aria-label": "Notifications",
              className: "relative rounded-lg p-2 text-slate-400 hover:bg-white/[0.04] hover:text-white"
            },
              e(Bell, { size: 16 }),
              runtimeNotifications.filter(n => n.unread).length > 0 && e("span", { className: "absolute right-1 top-1 h-2 w-2 rounded-full bg-rose-500" })
            ),
            // V6.14: User Menu(整个 wrap 加 onClick + ref)
            e("button", {
              ref: userMenuBtnRef,
              onClick: () => setShowUserMenu(true),
              "aria-label": "User Menu",
              className: "flex items-center gap-2 border-l border-white/[0.06] pl-3 rounded-r-lg hover:bg-white/[0.02] py-1 pr-2"
            },
              e(Avatar, {
        src: viewingAs ? null : currentUser.avatarUrl,
        alt: currentUser.name, size: 32, 
        fallback: viewingAs ? viewingAs.avatar : currentUser.avatar,
        gradient: viewingAs ? viewingAs.color : currentUser.avatarGradient
              }),
              e("div", { className: "hidden text-xs sm:block text-left" },
                e("div", { className: "text-white flex items-center gap-1" }, 
                  viewingAs ? viewingAs.name : currentUser.name,
                  viewingAs && e("span", { className: "text-[8px] px-1 py-0.5 rounded bg-blue-500/20 text-blue-300" }, t("正在以"))
                ),
                e("div", { className: "text-slate-500" }, 
                  viewingAs ? viewingAs.title : (currentUser.role === "admin" ? t("Admin") : t("员工"))
                )
              ),
              e(ChevronDown, { size: 14, className: "hidden text-slate-500 sm:block" })
            )
          )
        ),

        // ─── ROUTING ───
        e(AnimatePresence, { mode: "wait", initial: false },
          e(motion.div, {
            key: activeNav,
            className: "min-h-[calc(100vh-4rem)]",
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
            }),

            activeNav === "my-kol" && e(React.Suspense, {
              fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "MY KOL 加载中...")
            },
              e(MyKolPage, {
                apiToken,
                viewMode: appViewMode,
                data: dashboardData,
                userName,
                userRole,
                onRefreshData,
                onSelectPage: handleReplicaSelectPage,
              })
            ),

            activeNav === "projects" && e(React.Suspense, {
              fallback: e("div", { className: "min-h-[60vh] p-8 text-[12px] text-slate-400" }, "Projects 加载中...")
            },
              e(LegacyProjectsPage, {
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
                })
              )
            ),

            activeNav === "shopify" && e(ShopifyConnectPage, { apiToken }),

            // Placeholder for nav items not yet built
            activeNav !== "dashboard" && activeNav !== "kol-pool" && activeNav !== "my-kol" && activeNav !== "projects" && activeNav !== "events" && activeNav !== "shopify" && e("div", { className: "p-8 md:p-16 flex flex-col items-center justify-center text-center min-h-[60vh]" },
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
              kpiScope, setKpiScope, t, setSelectedKpi, globeContainerRef, isAvailable, pins, currentMode,
              venue, item, city, country, setPreviewEvent, handleCountryChange, handleCityChange, handleItemChange,
              setVenue, setSelectedPin, viewMode, setViewMode, countryOptions, cityOptions, itemOptions, venueOptions,
              breadcrumb, goBack, topListData, setSelectedEvent, setSelectedSignal, setShowAllSignals, setShowAIConfirm,
              setAiRegenerating, aiRegenerating, setSelectedMover, setShowAllMovers, setSelectedProject, setShowAllProjects,
              setSelectedPublish, setShowFullCalendar, focusTarget,
              viewModes: runtimeViewModes,
              metrics: dashboardRuntime.metrics,
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
            })
          )
        )
    )
  )
  )
  );
}
