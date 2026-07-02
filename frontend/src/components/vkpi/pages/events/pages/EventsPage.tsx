import React, { useEffect, useMemo, useState } from "react";
import { Activity, CircleDot, DollarSign, Package, Plus, Search, Target, TrendingUp } from "lucide-react";
import EventCard from "../components/EventCard";
import {
  listEvents, createEvent, updateEvent, deleteEvent,
  toUiEvent, fromUiCreate, fromUiUpdate, unwrapItem,
  type UiEvent, type VkpiEvent,
} from "../../../../../services/vkpi/events-api";
import { listInventory, toUiStock } from "../../../../../services/vkpi/inventory-api";
import { apiFetch } from "../../../../../services/http";
import { setRealProjects, setRealStaff } from "../shared/lookups";
import { useAuth } from "../../../../../hooks/useAuth";
import DeleteConfirmModal from "../modals/DeleteConfirmModal";
import NewEventModal, { type NewEventSubmit } from "../modals/NewEventModal";
import StockManagerModal from "../modals/StockManagerModal";
import EventDetailView from "./EventDetailView";
import { EVENT_STATUS, EVENT_TYPES } from "../shared/constants";
import { fmtMoneyShort, sum } from "../shared/helpers";
import type { CurrentUserVm, EventVm, StockItem, UiStaff } from "../shared/types";

const e = React.createElement;

// toUiEvent 输出的 UiEvent 与本地宽松 EventVm 在运行时同形;
// budgetByCategory(unknown vs BudgetCell)等仅类型口径不同 → 经统一 VM 适配。
function asEventVm(u: UiEvent): EventVm {
  return u as unknown as EventVm;
}

interface EventsPageProps {
  currentUser: CurrentUserVm;
  staff?: UiStaff[];
  initialEventId?: string | null;
  onConsumeInitialEvent?: () => void;
}

export default function EventsPage({ currentUser, staff = [], initialEventId = null, onConsumeInitialEvent }: EventsPageProps) {
  const { token } = useAuth();
  // 真实 staff 喂 lookups 模块缓存(渲染前同步设置,深层组件的 ownerById/ownerByInitial 才能解析真人;
  // 幂等赋值,与 setRealProjects 同款模式)。
  setRealStaff(staff);
  const [events, setEvents] = useState<EventVm[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [editingEvent, setEditingEvent] = useState<EventVm | null>(null);
  const [deletingEvent, setDeletingEvent] = useState<EventVm | null>(null);
  const [showStock, setShowStock] = useState(false);
  const [stock, setStock] = useState<StockItem[]>([]);
  const [statusFilter, setStatusFilter] = useState("全部");
  const [typeFilter, setTypeFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [showOnlyMine, setShowOnlyMine] = useState(false);

  // 从后端加载真活动(替代旧 mock EVENTS_DATA)
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError("");
    listEvents(token, { limit: 200 })
      .then(res => { if (alive) setEvents((res.items || []).map(toUiEvent).map(asEventVm)); })
      .catch(err => { if (alive) { setEvents([]); setLoadError(String(err && err.message ? err.message : err)); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [token]);

  // #7 拉真项目喂活动「关联项目」选择器 + lookups.projectById 模块缓存(卡片/详情正确显名)。失败回退 mock。
  const [projectOptions, setProjectOptions] = useState<Array<Record<string, any>>>([]);
  useEffect(() => {
    let alive = true;
    apiFetch<{ projects?: Array<Record<string, any>> }>("/api/admin/vkpi/projects?limit=200", { timeoutMs: 6000 }, token)
      .then((resp) => {
        if (!alive) return;
        const items = Array.isArray(resp?.projects) ? resp.projects : [];
        setProjectOptions(items);
        setRealProjects(items);
      })
      .catch(() => { /* 保留 mock 兜底 */ });
    return () => { alive = false; };
  }, [token]);

  // 从后端加载真·公司库存。#8 去 mock:初始空数组,失败显空(不再回退假 INITIAL_STOCK)。
  useEffect(() => {
    let alive = true;
    listInventory(token)
      .then(res => { if (alive && Array.isArray(res.items)) setStock(res.items.map(toUiStock) as StockItem[]); })
      .catch(() => { /* 失败保持空,前端诚实显示无库存(非假数据) */ });
    return () => { alive = false; };
  }, [token]);

  // dashboard「查看完整报告/编辑」跳转带来的活动 id:活动加载完成后自动打开其详情,
  // 随即清空(consume),以免返回列表再进又被自动弹回。找不到则静默忽略。
  useEffect(() => {
    if (!initialEventId || loading) return;
    if (events.some(ev => String(ev.id) === String(initialEventId))) {
      setSelectedId(String(initialEventId));
    }
    if (onConsumeInitialEvent) onConsumeInitialEvent();
  }, [initialEventId, loading, events, onConsumeInitialEvent]);

  function handleCreateEvent(data: NewEventSubmit) {
    setShowNew(false);
    createEvent(token, fromUiCreate(data))
      .then(res => { const row = unwrapItem<VkpiEvent>(res); if (row) setEvents(prev => [asEventVm(toUiEvent(row)), ...prev]); })
      .catch(err => { setLoadError("创建失败:" + String(err && err.message ? err.message : err)); });
  }

  function handleUpdateEvent(updated: EventVm) {
    setEditingEvent(null);
    setEvents(prev => prev.map(ev => ev.id === updated.id ? { ...ev, ...updated, updatedAt: "刚刚" } : ev)); // 乐观
    updateEvent(token, updated.id, fromUiUpdate(updated))
      .then(res => { const row = unwrapItem<VkpiEvent>(res); if (row) setEvents(prev => prev.map(ev => ev.id === row.id ? asEventVm(toUiEvent(row)) : ev)); })
      .catch(err => { setLoadError("更新失败:" + String(err && err.message ? err.message : err)); });
  }

  function handleDeleteEvent(id: string) {
    setDeletingEvent(null);
    setSelectedId(null);
    setEvents(prev => prev.filter(ev => ev.id !== id)); // 乐观
    deleteEvent(token, id)
      .catch(err => { setLoadError("删除失败:" + String(err && err.message ? err.message : err)); });
  }

  const STATUS_LIST = ["全部", "筹备中", "物料就绪", "进行中", "复盘中", "已完成"];

  // 权限: showOnlyMine 时只显示当前用户参与的 event
  const filtered = useMemo(() => events.filter(ev => {
    if (showOnlyMine && !(ev.teamUserIds || []).includes(currentUser.id)) return false;
    if (statusFilter !== "全部" && (EVENT_STATUS[ev.status]?.label || ev.status) !== statusFilter) return false;
    if (typeFilter !== "all" && ev.typeKey !== typeFilter) return false;
    if (search && !ev.title.toLowerCase().includes(search.toLowerCase()) && !(ev.location?.city || "").toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  }), [events, statusFilter, typeFilter, search, showOnlyMine, currentUser]);

  // KPI - 也按权限过滤
  const myEvents = events.filter(ev => (ev.teamUserIds || []).includes(currentUser.id));
  const activeEvents = myEvents.filter(ev => ev.status === "planning" || ev.status === "prep_ready" || ev.status === "live");
  const totalSpentThisMonth = myEvents.reduce((s, ev) => s + sum(ev.budgetByCategory, "spent"), 0);
  // 跨 event 的「我的待办」需逐 event 拉详情(N+1),代价过高;此处不再用 mock TASKS_DATA
  // 充数。真实逐 event 待办计数在各 Event 详情页「概览/任务」tab 内呈现(真后端)。
  const doneEvents = myEvents.filter(ev => ev.status === "done");
  const avgRoi = doneEvents.length > 0 ? (doneEvents.reduce((s, ev) => s + (ev.roi || 0), 0) / doneEvents.length) : 0;

  if (selectedId) {
    const ev = events.find(x => x.id === selectedId);
    if (!ev) { setSelectedId(null); return null; }
    return e(React.Fragment, null,
      e(EventDetailView, {
        ev, currentUser, staff, token,
        onBack: () => setSelectedId(null),
        onEdit: () => setEditingEvent(ev),
        onDelete: () => setDeletingEvent(ev),
        onUpdateTeam: (newTeamIds: string[]) => {
          setEvents(prev => prev.map(x => x.id === ev.id ? { ...x, teamUserIds: newTeamIds, updatedAt: "刚刚" } : x)); // 乐观
          updateEvent(token, ev.id, { team_ids: newTeamIds }) // 落库(原仅本地 state，刷新即丢=数据丢失 bug)
            .catch(err => { setLoadError("团队更新失败:" + String(err && err.message ? err.message : err)); });
        },
        // 复盘 tab 已落库的结果/状态:把后端真值同步进列表(banner/卡片即时反映,非等整页刷新)
        onEventPatched: (uiRow: UiEvent) => {
          if (uiRow && uiRow.id) setEvents(prev => prev.map(x => x.id === uiRow.id ? { ...x, ...asEventVm(uiRow) } : x));
        },
        stock, setStock,
      }),
      editingEvent && e(NewEventModal, {
        initialData: editingEvent,
        teamOptions: staff,
        projects: projectOptions,
        onClose: () => setEditingEvent(null),
        onSubmit: (data: NewEventSubmit) => handleUpdateEvent({ ...editingEvent, ...data, budgetTotal: data.budget,
          location: { ...editingEvent.location, name: data.locName, city: data.city, country: data.country },
          teamUserIds: data.teamIds, relatedProjectIds: data.projectIds,
        }),
      }),
      deletingEvent && e(DeleteConfirmModal, {
        title: `删除 "${deletingEvent.title}"?`,
        subtitle: "所有费用 / 任务 / 物料数据将一起删除 · 不可撤销",
        onClose: () => setDeletingEvent(null),
        onConfirm: () => handleDeleteEvent(deletingEvent.id),
      })
    );
  }

  return e("div", { className: "max-w-7xl mx-auto p-5" },
    // 加载 / 错误 横幅(真后端状态)
    loadError && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200" }, "⚠ ", loadError),
    loading && e("div", { className: "mb-3 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] text-[11px] text-slate-400" }, "加载活动中…"),
    // 顶部 KPI 4 个
    e("div", { className: "grid grid-cols-4 gap-3 mb-5" },
      e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-3.5" },
        e("div", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-400 mb-2" }, e(Activity, { size: 11, className: "text-purple-400" }), "我参与的进行中"),
        e("div", { className: "flex items-baseline gap-1.5" },
          e("div", { className: "text-[22px] font-bold text-white tabular-nums" }, activeEvents.length),
          e("div", { className: "text-[11px] text-slate-500" }, "/ ", myEvents.length, " 我的")
        )
      ),
      e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-3.5" },
        e("div", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-400 mb-2" }, e(DollarSign, { size: 11, className: "text-emerald-400" }), "我参与的已花"),
        e("div", { className: "flex items-baseline gap-1.5" },
          e("div", { className: "text-[22px] font-bold text-white tabular-nums" }, fmtMoneyShort(totalSpentThisMonth)),
          e("div", { className: "text-[11px] text-emerald-300 flex items-center gap-0.5" }, e(TrendingUp, { size: 10 }), "+12%")
        )
      ),
      e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-3.5" },
        e("div", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-400 mb-2" }, e(CircleDot, { size: 11, className: "text-amber-400" }), "我的待办"),
        e("div", { className: "flex items-baseline gap-1.5" },
          e("div", { className: "text-[22px] font-bold text-slate-500 tabular-nums" }, "—"),
          e("div", { className: "text-[11px] text-slate-500" }, "进入 Event 查看")
        )
      ),
      e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-3.5" },
        e("div", { className: "flex items-center gap-1.5 text-[10.5px] text-slate-400 mb-2" }, e(Target, { size: 11, className: "text-cyan-400" }), "已完成平均 ROI"),
        e("div", { className: "flex items-baseline gap-1.5" },
          e("div", { className: "text-[22px] font-bold text-white tabular-nums" }, avgRoi.toFixed(1), "x"),
          e("div", { className: "text-[11px] text-slate-500" }, doneEvents.length, " event")
        )
      )
    ),

    // 标题 + 筛选
    e("div", { className: "flex items-center justify-between mb-4 flex-wrap gap-3" },
      e("div", { className: "flex items-center gap-3" },
        e("h1", { className: "text-[20px] font-bold text-white" }, "Events"),
        e("span", { className: "text-[11px] text-slate-500" }, filtered.length, " 个"),
        // 权限切换
        e("div", { className: "flex items-center gap-0.5 rounded-md border border-white/[0.06] p-0.5" },
          ([
            ["我参与的", true],
            ["全部", false],
          ] as Array<[string, boolean]>).map(([l, val]) => e("button", {
            key: l, onClick: () => setShowOnlyMine(val),
            className: `px-2 py-1 rounded text-[10.5px] font-medium transition-all ${showOnlyMine === val ? "bg-purple-500/20 text-purple-200" : "text-slate-500 hover:text-slate-300"}`
          }, l))
        )
      ),
      e("div", { className: "flex items-center gap-2" },
        e("div", { className: "relative" },
          e(Search, { size: 11, className: "absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" }),
          e("input", { type: "text", value: search, onChange: (ev: React.ChangeEvent<HTMLInputElement>) => setSearch(ev.target.value), placeholder: "搜索 event / 城市",
            className: "pl-7 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40 w-44" })
        ),
        e("select", { value: statusFilter, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setStatusFilter(ev.target.value),
          className: "px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-purple-500/40" },
          STATUS_LIST.map(s => e("option", { key: s, value: s, style: { background: "#0a0a0d" } }, s))
        ),
        e("select", { value: typeFilter, onChange: (ev: React.ChangeEvent<HTMLSelectElement>) => setTypeFilter(ev.target.value),
          className: "px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-purple-500/40" },
          e("option", { value: "all", style: { background: "#0a0a0d" } }, "全部类型"),
          Object.entries(EVENT_TYPES).map(([k, cfg]) => e("option", { key: k, value: k, style: { background: "#0a0a0d" } }, cfg.label))
        ),
        e("button", {
          onClick: () => setShowStock(true),
          className: "px-2.5 py-1.5 rounded-md bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 text-emerald-300 text-[11px] font-medium flex items-center gap-1.5",
          title: "管理公司库存 (跨 Event 共享)"
        }, e(Package, { size: 11 }), "公司库存 ", e("span", { className: "text-emerald-400/70 tabular-nums" }, "(", stock.length, ")")),
        e("button", {
          onClick: () => setShowNew(true),
          className: "px-3 py-1.5 rounded-md bg-purple-500/90 hover:bg-purple-500 text-white text-[11px] font-medium flex items-center gap-1.5"
        }, e(Plus, { size: 12 }), "新建 Event")
      )
    ),

    // grid
    e("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3" },
      filtered.length === 0
        ? e("div", { className: "col-span-3 p-12 text-center rounded-2xl border border-white/[0.06] bg-white/[0.01]" },
            e("div", { className: "text-slate-500 text-[12px]" }, "没有匹配的 event"),
            showOnlyMine && e("button", { onClick: () => setShowOnlyMine(false), className: "mt-2 text-[10.5px] text-purple-300 hover:text-purple-200" }, "查看全部 event →")
          )
        : filtered.map(ev => e(EventCard, { key: ev.id, ev, staff, onOpen: (x: EventVm) => setSelectedId(x.id) }))
    ),

    showNew && e(NewEventModal, {
      teamOptions: staff,
      currentUserId: currentUser?.id,
      projects: projectOptions,
      onClose: () => setShowNew(false),
      onSubmit: handleCreateEvent,
    }),
    showStock && e(StockManagerModal, { stock, setStock, token, onClose: () => setShowStock(false) })
  );
}
