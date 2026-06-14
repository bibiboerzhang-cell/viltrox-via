import React, { useEffect, useMemo, useState } from "react";
import { Activity, CircleDot, DollarSign, Package, Plus, Search, Target, TrendingUp } from "lucide-react";
import EventCard from "../components/EventCard.js";
import { INITIAL_STOCK } from "../data/stock.js";
import {
  listEvents, createEvent, updateEvent, deleteEvent,
  toUiEvent, fromUiCreate, fromUiUpdate, unwrapItem,
} from "../../../../../services/vkpi/events-api";
import { useAuth } from "../../../../../hooks/useAuth";
import { TASKS_DATA } from "../data/tasks.js";
import DeleteConfirmModal from "../modals/DeleteConfirmModal.js";
import NewEventModal from "../modals/NewEventModal.js";
import StockManagerModal from "../modals/StockManagerModal.js";
import EventDetailView from "./EventDetailView.js";
import { EVENT_STATUS, EVENT_TYPES } from "../shared/constants.js";
import { fmtMoneyShort, sum } from "../shared/helpers.js";

const e = React.createElement;
export default function EventsPage({ currentUser, staff = [] }) {
  const { token } = useAuth();
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [showNew, setShowNew] = useState(false);
  const [editingEvent, setEditingEvent] = useState(null);
  const [deletingEvent, setDeletingEvent] = useState(null);
  const [showStock, setShowStock] = useState(false);
  const [stock, setStock] = useState(INITIAL_STOCK);
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
      .then(res => { if (alive) setEvents((res.items || []).map(toUiEvent)); })
      .catch(err => { if (alive) { setEvents([]); setLoadError(String(err && err.message ? err.message : err)); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [token]);

  function handleCreateEvent(data) {
    setShowNew(false);
    createEvent(token, fromUiCreate(data))
      .then(res => { const row = unwrapItem(res); if (row) setEvents(prev => [toUiEvent(row), ...prev]); })
      .catch(err => { setLoadError("创建失败:" + String(err && err.message ? err.message : err)); });
  }

  function handleUpdateEvent(updated) {
    setEditingEvent(null);
    setEvents(prev => prev.map(ev => ev.id === updated.id ? { ...ev, ...updated, updatedAt: "刚刚" } : ev)); // 乐观
    updateEvent(token, updated.id, fromUiUpdate(updated))
      .then(res => { const row = unwrapItem(res); if (row) setEvents(prev => prev.map(ev => ev.id === row.id ? toUiEvent(row) : ev)); })
      .catch(err => { setLoadError("更新失败:" + String(err && err.message ? err.message : err)); });
  }

  function handleDeleteEvent(id) {
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
  const allTasks = myEvents.flatMap(ev => (TASKS_DATA[ev.id] || []).filter(t => 
    t.owner === "All" || t.owner === currentUser.initial || (t.collaborators || []).includes(currentUser.initial)
  ));
  const pendingTasks = allTasks.filter(t => !t.done).length;
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
        onUpdateTeam: (newTeamIds) => setEvents(prev => prev.map(x => x.id === ev.id ? { ...x, teamUserIds: newTeamIds, updatedAt: "刚刚" } : x)),
        stock, setStock,
      }),
      editingEvent && e(NewEventModal, {
        initialData: editingEvent,
        teamOptions: staff,
        onClose: () => setEditingEvent(null),
        onSubmit: data => handleUpdateEvent({ ...editingEvent, ...data, budgetTotal: data.budget,
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
          e("div", { className: "text-[22px] font-bold text-white tabular-nums" }, pendingTasks),
          e("div", { className: "text-[11px] text-slate-500" }, "项")
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
          [
            ["我参与的", true],
            ["全部", false],
          ].map(([l, val]) => e("button", {
            key: l, onClick: () => setShowOnlyMine(val),
            className: `px-2 py-1 rounded text-[10.5px] font-medium transition-all ${showOnlyMine === val ? "bg-purple-500/20 text-purple-200" : "text-slate-500 hover:text-slate-300"}`
          }, l))
        )
      ),
      e("div", { className: "flex items-center gap-2" },
        e("div", { className: "relative" },
          e(Search, { size: 11, className: "absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" }),
          e("input", { type: "text", value: search, onChange: ev => setSearch(ev.target.value), placeholder: "搜索 event / 城市",
            className: "pl-7 pr-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-white placeholder-slate-600 focus:outline-none focus:border-purple-500/40 w-44" })
        ),
        e("select", { value: statusFilter, onChange: ev => setStatusFilter(ev.target.value),
          className: "px-3 py-1.5 rounded-md bg-white/[0.02] border border-white/[0.06] text-[11px] text-slate-300 focus:outline-none focus:border-purple-500/40" },
          STATUS_LIST.map(s => e("option", { key: s, value: s, style: { background: "#0a0a0d" } }, s))
        ),
        e("select", { value: typeFilter, onChange: ev => setTypeFilter(ev.target.value),
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
        : filtered.map(ev => e(EventCard, { key: ev.id, ev, staff, onOpen: x => setSelectedId(x.id) }))
    ),
    
    showNew && e(NewEventModal, {
      teamOptions: staff,
      currentUserId: currentUser?.id,
      onClose: () => setShowNew(false),
      onSubmit: handleCreateEvent,
    }),
    showStock && e(StockManagerModal, { stock, setStock, onClose: () => setShowStock(false) })
  );
}
