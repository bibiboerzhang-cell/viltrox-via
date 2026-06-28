// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, ChevronDown, DollarSign, FileText, Globe2, HelpCircle, List, Loader2, Menu, MessageCircle, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, TrendingUp, User, X } from "lucide-react";
import "./styles/mockup.css";
import "../styles/vkpi-settings-dark.css";
import { KOLPoolPage } from "./KOLPoolPage";
import { ShopifyConnectPage } from "./ShopifyConnectPage";
import { ShopifyHubPage } from "../pages/ShopifyHubPage";
import { DealerMapPage } from "../pages/DealerMapPage";
import { DashboardReplicaPage } from "./DashboardReplicaPage";
import { V615Sidebar } from "./V615Sidebar";
import { V615Topbar } from "./V615Topbar";
import { usePermissions } from "../../../hooks/usePermissions";
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
import { getDealerLocations } from "../../../services/vkpi/dealers-api";
import { normalizeEventsHierarchy, normalizeDealersHierarchy } from "./normalizers";
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
import {
  buildMappedEvents,
  buildUpcomingEvents,
  buildEventPins,
  buildPins,
  buildFocusTarget,
  buildTopListData,
  buildCountryOptions,
  buildCityOptions,
  buildItemOptions,
  buildVenueOptions,
} from "./V615ReplicaApp.helpers";

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
  const [activeNav, setActiveNav] = useState(["dashboard", "kol-pool", "my-kol", "projects", "events", "shopify", "dealers"].includes(initialNav) ? initialNav : "dashboard");
  const [theme, setTheme] = useState(stored.theme || "dark");

  // 板块授权守卫:stored/程序化导航落到「被该成员隐藏」的板块时弹回 dashboard。
  // 侧栏已隐藏入口(V615Sidebar 按 canViewBoard 过滤),此处为 stored state / 直链兜底。owner 全见不受影响。
  const boardPerms = usePermissions();
  useEffect(() => {
    if (!boardPerms.canViewBoard(activeNav)) setActiveNav("dashboard");
  }, [activeNav]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const handleOpenProjectTask = (event) => {
      const projectId = String(event?.detail?.projectId || "");
      if (projectId) setOpenLegacyProjectId(projectId);
      setActiveNav("projects");
    };
    // item1(2026-06-16):video/账号档案任务点开 → 切到 KOL Pool 板块(pending id 由 board 写 localStorage,
    // KOLPoolPage 挂载后消费并按 id 开抽屉)。
    const handleOpenKolPoolItem = () => {
      setActiveNav("kol-pool");
    };
    window.addEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
    window.addEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
    window.addEventListener("vkpi:open-project-task", handleOpenProjectTask);
    window.addEventListener("vkpi:open-kol-pool-item", handleOpenKolPoolItem);
    return () => {
      window.removeEventListener("vkpi:open-kol-search-session", handleOpenKolSearchSession);
      window.removeEventListener("vkpi:open-mykol-kol", handleOpenMyKolKol);
      window.removeEventListener("vkpi:open-project-task", handleOpenProjectTask);
      window.removeEventListener("vkpi:open-kol-pool-item", handleOpenKolPoolItem);
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
  // 从 dashboard「查看完整报告/编辑」跳到 Events 页时,带上要自动打开的活动 id。
  const [pendingEventId, setPendingEventId] = useState(null);
  // 2026-06-14 诚实化:Upcoming Events 卡接真实 /api/admin/vkpi/events,不再传空数组。
  const [eventRows, setEventRows] = useState([]);
  const [dealerPins, setDealerPins] = useState<any[]>([]);
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
    // 创建已 200 成功;刷新列表失败不该被上层 catch 当成"创建失败"(此前的假失败真因)。
    try { await refreshReplicaProjects(); } catch { /* 刷新打嗝不影响创建成功 */ }
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
  const currentMode = viewMode ? runtimeViewModes[viewMode] : null;
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
  const openEventsPage = useCallback((eventId = null) => {
    setPendingEventId(eventId ? String(eventId) : null);
    saveStoredState({ activeNav: "events" });
    setActiveNav("events");
  }, []);

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
    e(AnimatePresence, null, selectedEvent && e(EventDetailModal, {
      event: selectedEvent,
      onClose: () => setSelectedEvent(null),
      // 「编辑方案 / 查看完整报告」→ 跳真实 Events 页并自动打开该活动详情(含真实 tab + 编辑)。
      onOpenFullEvent: () => {
        const id = selectedEvent?.id || selectedEvent?.raw?.id;
        setSelectedEvent(null);
        openEventsPage(id);
      },
    })),
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
    e(AnimatePresence, null, reportOpen && e(ReportPanel, { onClose: () => setReportOpen(false), data: reportData, apiToken })),
    // V6.11: Signal Detail Modal
    e(AnimatePresence, null, selectedSignal && e(SignalDetailModal, { alert: selectedSignal, onClose: () => setSelectedSignal(null) })),
    // V6.13: 新 modal mounts
    e(AnimatePresence, null, selectedProject && e(ProjectDetailModal, {
      project: selectedProject,
      staff: uiStaff,
      apiToken,
      // 分享按钮门控:owner/admin 才能管理成员(非授权后端 403 兜底,UI 也先隐藏)。
      canManage: ["admin", "owner"].includes(String(currentUser?.role || "").toLowerCase()),
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
    e(AnimatePresence, null, selectedPublish && e(PublishPreviewModal, { item: selectedPublish, apiToken, onClose: () => setSelectedPublish(null) })),
    e(AnimatePresence, null, selectedMover && e(KOLDetailModal, {
      mover: selectedMover,
      apiToken,
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
      // 2026-06-15 P0-3:AI 决策卡与 inbox action 无 1:1 映射,改为诚实引导到 Dashboard 右栏「今日建议」面板逐条审批(approve)
      onConfirm: () => {
        setShowAIConfirm(false);
        saveStoredState({ activeNav: "dashboard" });
        setActiveNav("dashboard");
        pushLocalNotification({
          id: `ai-confirm-${Date.now()}`,
          raw: {},
          iconKey: "bell",
          iconColor: "#a855f7",
          title: "已转到「今日建议」",
          desc: "AI 决策需在 Dashboard 右侧「今日建议」面板逐条审批通过(approve)后由后端执行。",
          time: "刚刚",
          unread: true,
          category: "notification",
          severity: "low",
          status: "todo",
          priority: "low",
          source: "ai_confirm_guide",
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
      anchorRef: messagesBtnRef, t, viewingAs, apiToken,
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
    e(AnimatePresence, null, showProfile && e(ProfileModal, { user: currentUser, onClose: () => setShowProfile(false), t, apiToken })),
    e(AnimatePresence, null, showTeam && e(TeamModal, {
      user: currentUser, staff: uiStaff, groups: staffGroups, apiToken,
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
      },
      onNavigate: (linked: any) => {
        const ty = String(linked?.type || "").toLowerCase();
        const tab = (ty === "project" || ty === "assignment") ? "projects"
          : (ty === "kol" || ty === "creator" || ty === "kol_pool") ? "kol-pool"
          : (ty === "event") ? "events"
          : "dashboard";
        saveStoredState({ activeNav: tab });
        setSelectedNotif(null);
        setActiveNav(tab);
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
      allEvents: mappedEvents,
      onClose: () => setPreviewEvent(null),
      onViewDetails: (evt) => {
        setPreviewEvent(null);
        setSelectedEvent(evt);
      }
    })),

    e("div", { className: "v615-shell relative mx-auto flex min-h-screen max-w-[1920px]" },

      // ─── Sidebar ───(保守拆:纯展示叶子区块抽到 V615Sidebar,props 显式传递,行为不变)
      e(V615Sidebar, {
        collapsed, setCollapsed, activeNav, setActiveNav, theme, setTheme, versionBadge, apiToken,
      }),

      // ─── Main ───
      e("main", { className: "min-w-0 flex-1" },

        // Header(保守拆:纯展示顶栏抽到 V615Topbar,props 显式传递,行为不变)
        e(V615Topbar, {
          activeNav, helpBtnRef, setShowHelp, messagesBtnRef, setShowMessages, activeReminders,
          setReportOpen, notifsBtnRef, setShowNotifs, runtimeNotifications, userMenuBtnRef,
          setShowUserMenu, viewingAs, currentUser, t,
        }),

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
              staff: uiStaff,
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
                  // dashboard 跳转带来的活动 id:打开后即清空,避免返回再进又自动弹。
                  initialEventId: pendingEventId,
                  onConsumeInitialEvent: () => setPendingEventId(null),
                })
              )
            ),

            activeNav === "shopify" && e(ShopifyHubPage, { apiToken }),
            activeNav === "dealers" && e(DealerMapPage, { apiToken }),

            // Placeholder for nav items not yet built
            activeNav !== "dashboard" && activeNav !== "kol-pool" && activeNav !== "my-kol" && activeNav !== "projects" && activeNav !== "events" && activeNav !== "shopify" && activeNav !== "dealers" && e("div", { className: "p-8 md:p-16 flex flex-col items-center justify-center text-center min-h-[60vh]" },
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
              showSettingsModal,  // P2 穿透:设置浮层打开时,dashboard 浮卡/地图 overlay 不渲染
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
              // Upcoming Events 卡「View all events →」→ 跳真实 Events 页。
              onOpenEvents: () => openEventsPage(null),
            })
          )
        )
    )
  )
  )
  );
}
