// Presentational overlay mounts extracted verbatim from CockpitApp.tsx (行为不变抽取).
// 这一大坨 e(AnimatePresence, ...) 模态 / popover 挂载原本平铺在容器 return 里;
// 现以显式 props 的纯展示组件形式上移,JSX 逐字不变,所有 state / setter / 派生值由容器透传。
// 容器本体与全部 hooks 顺序保持在 CockpitApp.tsx,行为必然不变。

import React from "react";
import { AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { AllMoversModal } from "./components/modals/AllMoversModal";
import { AllNotificationsModal } from "./components/modals/AllNotificationsModal";
import { AllProjectsModal } from "./components/modals/AllProjectsModal";
import { AllRemindersModal } from "./components/modals/AllRemindersModal";
import { EventPreviewModal } from "./components/modals/EventPreviewModal";
import { FeedbackModal } from "./components/modals/FeedbackModal";
import { FullCalendarModal } from "./components/modals/FullCalendarModal";
import { KOLDetailModal } from "./components/modals/KOLDetailModal";
import { NotificationDetailModal } from "./components/modals/NotificationDetailModal";
import { PinDetailModal } from "./components/modals/PinDetailModal";
import { ProfileModal } from "./components/modals/ProfileModal";
import { PublishPreviewModal } from "./components/modals/PublishPreviewModal";
import { ReportPanel } from "./components/ReportPanel";
import { SettingsPage } from "../pages/SettingsPage";
import { AuthorizationOverlay } from "../pages/settings/AuthorizationOverlay";
import { ShortcutsModal } from "./components/modals/ShortcutsModal";
import { SignalsAllModal } from "./components/modals/SignalsAllModal";
import { HelpPopover } from "./components/popovers/HelpPopover";
import { NotificationsPopover } from "./components/popovers/NotificationsPopover";
import { UserMenuPopover } from "./components/popovers/UserMenuPopover";
import { WorkRemindersPopover } from "./components/popovers/WorkRemindersPopover";
import { LazyErrorBoundary } from "./components/LazyErrorBoundary";
import { logoutCockpit, resolveCockpitAlert } from "./api";
import { saveStoredState } from "./lib/storage";
import { createStaffGroup, updateStaffGroup } from "../../../services/vkpi/groups-api";

const e = React.createElement;
const AITodayEvidenceModal = React.lazy(() => import("./components/modals/AITodayEvidenceModal").then((module) => ({ default: module.AITodayEvidenceModal })));
const EditGroupModal = React.lazy(() => import("./components/modals/EditGroupModal").then((module) => ({ default: module.EditGroupModal })));
const EventDetailModal = React.lazy(() => import("./components/modals/EventDetailModal").then((module) => ({ default: module.EventDetailModal })));
const KPIDetailModal = React.lazy(() => import("./components/modals/KPIDetailModal").then((module) => ({ default: module.KPIDetailModal })));
const ProjectDetailModal = React.lazy(() => import("./components/modals/ProjectDetailModal").then((module) => ({ default: module.ProjectDetailModal })));
const SignalDetailModal = React.lazy(() => import("./components/modals/SignalDetailModal").then((module) => ({ default: module.SignalDetailModal })));
const TeamModal = React.lazy(() => import("./components/modals/TeamModal").then((module) => ({ default: module.TeamModal })));
// 内部工作流 PDF 已移出构建产物(dist 减重 7.8MB),原件在仓库 docs/assets/ 下。
// 死链留档:/assets/vkpi_kol_workflow_cloud_demo_2026-06-30.pdf?v=20260630-1115
// 若日后需要重新对外提供,应走后端受控下载而非 public/ 静态目录。
const VKPI_KOL_WORKFLOW_GUIDE_URL: string | null = null;

function guardedLazyModal(name: string, onDismiss: () => void, node: React.ReactNode) {
  return e(
    LazyErrorBoundary,
    { name, variant: "overlay", onDismiss },
    e(React.Suspense, { fallback: null }, node),
  );
}

function openKolWorkflowGuide() {
  if (!VKPI_KOL_WORKFLOW_GUIDE_URL) return; // PDF 已移出 dist,链接置空;HelpPopover 侧会显示为禁用项
  const guideWindow = window.open(VKPI_KOL_WORKFLOW_GUIDE_URL, "_blank", "noopener,noreferrer");
  if (!guideWindow) window.location.assign(VKPI_KOL_WORKFLOW_GUIDE_URL);
}

// 返回容器 return 里那一长串 overlay 节点(数组)。在 CockpitApp 里以 ...CockpitOverlays({...}) 展开。
export function CockpitOverlays(p: any) {
  const {
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
    settingsInitialSection, setSettingsInitialSection, isOwnerUser,
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
  } = p;

  return [
    e(AnimatePresence, { key: "ov-pin" }, selectedPin && e(PinDetailModal, {
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
    e(AnimatePresence, { key: "ov-event" }, selectedEvent && guardedLazyModal("活动详情", () => setSelectedEvent(null), e(EventDetailModal, {
      event: selectedEvent,
      onClose: () => setSelectedEvent(null),
      // 「编辑方案 / 查看完整报告」→ 跳真实 Events 页并自动打开该活动详情(含真实 tab + 编辑)。
      onOpenFullEvent: () => {
        const id = selectedEvent?.id || selectedEvent?.raw?.id;
        setSelectedEvent(null);
        openEventsPage(id);
      },
    }))),
    e(AnimatePresence, { key: "ov-kpi" }, selectedKpi && guardedLazyModal("KPI 详情", () => setSelectedKpi(null), e(KPIDetailModal, {
      kpiId: selectedKpi,
      initialScope: kpiScope,
      // 【D4】per-staff scope 记忆:弹窗内切 scope 也按 staff id 存,防同浏览器多账号串号。
      staffId: currentUser?.id,
      metrics: dashboardRuntime.metrics,
      onClose: () => setSelectedKpi(null),
      onDrillToKolPool: () => {
        saveStoredState({ activeNav: "kol-pool" });
        setSelectedKpi(null);
        setActiveNav("kol-pool");
      },
    }))),
    // V6.10: Report Panel
    e(AnimatePresence, { key: "ov-report" }, reportOpen && e(ReportPanel, { onClose: () => setReportOpen(false), data: reportData, apiToken })),
    // V6.11: Signal Detail Modal
    e(AnimatePresence, { key: "ov-signal" }, selectedSignal && guardedLazyModal("信号详情", () => setSelectedSignal(null), e(SignalDetailModal, { alert: selectedSignal, onClose: () => setSelectedSignal(null) }))),
    // V6.13: 新 modal mounts
    e(AnimatePresence, { key: "ov-project" }, selectedProject && guardedLazyModal("项目详情", () => setSelectedProject(null), e(ProjectDetailModal, {
      project: selectedProject,
      staff: uiStaff,
      apiToken,
      // 分享按钮门控:owner/admin 才能管理成员(非授权后端 403 兜底,UI 也先隐藏)。
      canManage: ["admin", "owner"].includes(String(currentUser?.role || "").toLowerCase()),
      onAssigned: () => { onRefreshData && onRefreshData(); },
      onClose: () => setSelectedProject(null),
      onOpenFullPage: (project: any) => {
        const projectId = String(project?.projectId || project?.id || "");
        const row = (dashboardData.projects || []).find((item: any) => item.id === projectId);
        setSelectedLegacyProject(row || null);
        setOpenLegacyProjectId(projectId);
        setSelectedProject(null);
        saveStoredState({ activeNav: "projects" });
        setActiveNav("projects");
      },
    }))),
    e(AnimatePresence, { key: "ov-publish" }, selectedPublish && e(PublishPreviewModal, { item: selectedPublish, apiToken, onClose: () => setSelectedPublish(null) })),
    e(AnimatePresence, { key: "ov-mover" }, selectedMover && e(KOLDetailModal, {
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
    e(AnimatePresence, { key: "ov-allsignals" }, showAllSignals && e(SignalsAllModal, {
      alerts: dashboardRuntime.signals,
      onClose: () => setShowAllSignals(false),
      onAlertClick: (a: any) => { setShowAllSignals(false); setSelectedSignal(a); }
    })),
    e(AnimatePresence, { key: "ov-aiconfirm" }, showAIConfirm && guardedLazyModal("AI Today 证据", () => setShowAIConfirm(false), e(AITodayEvidenceModal, {
      insight: dashboardRuntime.aiInsight,
      onClose: () => setShowAIConfirm(false),
      onOpenKolPool: () => {
        setShowAIConfirm(false);
        saveStoredState({ activeNav: "kol-pool" });
        setActiveNav("kol-pool");
      }
    }))),
    // V6.14.2: 顶部 4 按钮 popovers - 锚定到具体按钮 + i18n + role
    e(AnimatePresence, { key: "ov-help" }, showHelp && e(HelpPopover, {
      onClose: () => setShowHelp(false),
      anchorRef: helpBtnRef, t,
      // PDF 已移出构建产物;传 undefined 让 HelpPopover 将「文档 & 指南」渲染为禁用项(disabled: !onOpenDocs)
      onOpenDocs: VKPI_KOL_WORKFLOW_GUIDE_URL ? openKolWorkflowGuide : undefined,
      onOpenShortcuts: () => setShowShortcuts(true),
      onOpenFeedback: () => setShowFeedback(true),
    })),
    e(AnimatePresence, { key: "ov-messages" }, showMessages && e(WorkRemindersPopover, {
      onClose: () => setShowMessages(false),
      reminders: activeReminders,
      anchorRef: messagesBtnRef, t, viewingAs, apiToken,
      onViewAll: () => setShowAllReminders(true),
    })),
    e(AnimatePresence, { key: "ov-notifs" }, showNotifs && e(NotificationsPopover, {
      onClose: () => setShowNotifs(false),
      notifications: runtimeNotifications,
      anchorRef: notifsBtnRef, t,
      onViewAll: () => setShowAllNotifs(true),
      onItemClick: (n: any) => setSelectedNotif(n),
    })),
    e(AnimatePresence, { key: "ov-usermenu" }, showUserMenu && e(UserMenuPopover, {
      onClose: () => setShowUserMenu(false),
      theme, onToggleTheme: () => setTheme((t: any) => t === "light" ? "dark" : "light"),
      anchorRef: userMenuBtnRef, t, user: currentUser, staff: uiStaff, lang,
      onToggleLang: () => setLang((l: any) => l === "zh" ? "en" : "zh"),
      viewingAs, onResetView: () => setViewingAs(null),
      onOpenProfile: () => setShowProfile(true),
      onOpenTeam: () => setShowTeam(true),
      onOpenSettings: () => { setSettingsInitialSection && setSettingsInitialSection(null); setShowSettingsModal(true); },
      // 授权页 V1.1(2026-07-11):「成员与授权」仅 owner 可见,直达独立浮层页
      // AuthorizationOverlay(demo 第二页同款),不再借道整页系统设置;
      // 设置页里的 staff 分区仍保留(头像菜单 + 设置页双入口,用户裁决)。
      isOwner: isOwnerUser,
      onOpenMembersAuth: () => setShowMembersAuth && setShowMembersAuth(true),
      onImpersonate: (s: any) => setViewingAs(s),
      onLogout: async () => {
        await logoutCockpit().catch(() => null);
        onSignOut && onSignOut();
      },
    })),
    // V6.14.2: 7 子 modals
    e(AnimatePresence, { key: "ov-profile" }, showProfile && e(ProfileModal, { user: currentUser, onClose: () => setShowProfile(false), t, apiToken })),
    e(AnimatePresence, { key: "ov-team" }, showTeam && guardedLazyModal("团队", () => setShowTeam(false), e(TeamModal, {
      user: currentUser, staff: uiStaff, groups: staffGroups, apiToken,
      onClose: () => setShowTeam(false),
      onImpersonate: (s: any) => setViewingAs(s),
      onOpenEditGroup: (g: any) => openGroupEditor("edit", g || null),
      onOpenNewGroup: () => openGroupEditor("new"),
      onRefreshGroups: refreshStaffGroups,
      t
    }))),
    showSettingsModal && e("div", { key: "ov-settings", className: "cockpit-settings-dark vkpi-settings-surface fixed inset-0 z-[1000] overflow-auto" },
      e("button", {
        type: "button",
        onClick: () => setShowSettingsModal(false),
        className: "vkpi-settings-surface__close fixed right-5 top-4 z-[210] grid h-9 w-9 place-items-center rounded-lg border border-line bg-card text-muted shadow-sm backdrop-blur hover:bg-accent-soft hover:text-ink",
        title: t("关闭"),
        "aria-label": t("关闭系统设置"),
      }, e(X, { size: 18 })),
      e(SettingsPage, {
        // key 随目标区变:设置页已开着时再点「成员与授权」也能重定位到 staff 区。
        key: settingsInitialSection || "default",
        data: dashboardData,
        viewMode: appViewMode === "employee" ? "employee" : "manager",
        apiToken,
        initialSection: settingsInitialSection || undefined,
        onOpenBusinessArea: (area: "shopify" | "dealers" | "events" | "gtmCommand") => {
          setShowSettingsModal(false);
          saveStoredState({ activeNav: area });
          setActiveNav(area);
        },
        onRefreshData,
      })
    ),
    // 授权页 V1.1:成员与授权独立浮层(仅 staff 内容,无设置侧栏/其他分区),容器机制同上。
    showMembersAuth && e(AuthorizationOverlay, {
      key: "ov-members-auth",
      data: dashboardData,
      apiToken,
      onRefreshData,
      t,
      onClose: () => setShowMembersAuth(false),
    }),
    e(AnimatePresence, { key: "ov-shortcuts" }, showShortcuts && e(ShortcutsModal, { onClose: () => setShowShortcuts(false) })),
    e(AnimatePresence, { key: "ov-feedback" }, showFeedback && e(FeedbackModal, { onClose: () => setShowFeedback(false), apiToken, onSubmitted: handleFeedbackSubmitted })),
    // V6.14.4: ViewAll modals + NotifDetail + EditGroup
    e(AnimatePresence, { key: "ov-allprojects" }, showAllProjects && e(AllProjectsModal, {
      campaigns: dashboardRuntime.campaigns,
      onClose: () => setShowAllProjects(false),
      onProjectClick: (c: any) => setSelectedProject(c)
    })),
    e(AnimatePresence, { key: "ov-allmovers" }, showAllMovers && e(AllMoversModal, {
      movers: dashboardRuntime.topMovers,
      onClose: () => setShowAllMovers(false),
      onMoverClick: (m: any) => setSelectedMover(m)
    })),
    e(AnimatePresence, { key: "ov-fullcal" }, showFullCalendar && e(FullCalendarModal, {
      days: dashboardRuntime.calendarDays,
      onClose: () => setShowFullCalendar(false),
      onItemClick: (item: any) => setSelectedPublish(item)
    })),
    e(AnimatePresence, { key: "ov-allreminders" }, showAllReminders && e(AllRemindersModal, {
      reminders: activeReminders,
      onClose: () => setShowAllReminders(false),
      viewingAs,
      // T3(2026-07-02):传 token 让「完成」走真后端 /alerts/{id}/resolve(「忽略」暂 localStorage)。
      apiToken
    })),
    e(AnimatePresence, { key: "ov-allnotifs" }, showAllNotifs && e(AllNotificationsModal, {
      notifications: runtimeNotifications,
      onClose: () => setShowAllNotifs(false),
      onNotifClick: (n: any) => setSelectedNotif(n)
    })),
    e(AnimatePresence, { key: "ov-notifdetail" }, selectedNotif && e(NotificationDetailModal, {
      notification: selectedNotif,
      onClose: () => setSelectedNotif(null),
      onMarkRead: async (id: any) => {
        if (!apiToken) return;
        await resolveCockpitAlert(apiToken, id).catch(() => null);
        setRuntimeNotifications((prev: any) => prev.map((item: any) => item.id === id ? { ...item, unread: false, status: "done" } : item));
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
    e(AnimatePresence, { key: "ov-editgroup" }, showEditGroup && guardedLazyModal("编辑分组", () => setShowEditGroup(false), e(EditGroupModal, {
      groupName: editGroupName,
      mode: editGroupMode,
      staff: uiStaff,
      initialMembers: editGroupTarget?.member_ids || [],
      initialDesc: editGroupTarget?.description || "",
      permissions: editGroupTarget?.permissions || null,
      onClose: () => setShowEditGroup(false),
      onSave: async (group: any) => {
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
        } catch (err: any) {
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
    }))),
    // V6.9: Event preview modal (二次点击逻辑)
    e(AnimatePresence, { key: "ov-preview" }, previewEvent && e(EventPreviewModal, {
      event: previewEvent,
      allEvents: mappedEvents,
      onClose: () => setPreviewEvent(null),
      onViewDetails: (evt: any) => {
        setPreviewEvent(null);
        setSelectedEvent(evt);
      }
    })),
  ];
}
