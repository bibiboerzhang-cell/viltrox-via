import React from "react";
import { Package, PencilLine, Plus } from "lucide-react";
import { EditableDashboardBoard, type DashboardModuleDefinition } from "../components/EditableDashboardBoard";
import {
  listEvents,
  createEvent,
  updateEvent,
  deleteEvent,
  toUiEvent,
  fromUiCreate,
  fromUiUpdate,
  unwrapItem,
  type UiEvent,
  type VkpiEvent,
} from "../../../../services/vkpi/events-api";
import { listInventory, toUiStock } from "../../../../services/vkpi/inventory-api";
import { apiFetch } from "../../../../services/http";
import { useAuth } from "../../../../hooks/useAuth";
import { setRealProjects, setRealStaff } from "../../pages/events/shared/lookups";
import type { CurrentUserVm, EventVm, StockItem, UiStaff as EventsUiStaff } from "../../pages/events/shared/types";
// 页级 staff prop 用宽松结构型(与旧 EventsMockupPage 同款):CockpitApp 传的是 staffAdapter.UiStaff,
// 缺 index signature 但字段兼容;喂给 events 内部件时收窄为 EventsUiStaff。
type UiStaff = { id: string | number; name?: string; email?: string; avatar?: string; role?: string };
import { ErrorCard, ModuleCard, PendingCard } from "./MarketVoicePage.modules";
import {
  ACTIVE_STATUSES,
  BudgetBarsBody,
  EventsKpiBand,
  StatusDonutBody,
  TeamLoadBody,
  TypeBarsBody,
  fmtMoneyFull,
  overlapsThisMonth,
  spentOf,
} from "./EventsBoardPage.charts";
import {
  BoardGridBody,
  DEFAULT_FILTERS,
  EventsListDialog,
  InventoryBody,
  MODULE_SOURCES,
  RetroResultsBody,
  UpcomingBody,
  filterEvents,
  upcomingEvents,
  type BoardFilters,
} from "./EventsBoardPage.modules";
import { EventDetailTakeover, EventsBoardModals } from "./EventsBoardPage.embeds";

// Events → 板块页范式改版(金样板 = MarketVoicePage 四件套 + MyKolBoardPage 五件套
//   embeds 收编手法 1:1 同构)。旧 pages/events/EventsMockupPage(+EventsPage)已退役
//   于 4bcc5c68c 后收尾波(2026-07-12),如需恢复从 git 历史找回;EventDetailView/
//   modals/tabs/shared 仍被本族 embeds 消费,保留。
//   页壳:pagehead(标题 + 活动数药丸徽 + 实时辉光点 + 公司库存 / 新建 Event /
//   编辑布局钮)+ EditableDashboardBoard(模块注册表 + 默认布局五行 + palette 备选)。
//   数据源(全真,零编造):
//     GET  /api/admin/vkpi/events          —— vkpi_events(服务端按身份裁剪:管理层
//          全量;员工 = owner / team_ids jsonb / vkpi_event_members 共享 / is_public)
//     GET  /api/admin/vkpi/inventory       —— vkpi_inventory 公司库存(384 行口径核实
//          2026-07-12;失败 → KPI 卡诚实 pending + 库存模块 ErrorCard,不再静默空)
//     GET  /api/admin/vkpi/projects        —— 关联项目显名 + 新建/编辑选择器(旧页同款)
//     详情/CRUD(任务/费用/邀约/物料/产品/复盘/团队/分享)全部住 EventDetailTakeover
//          内嵌旧组件,events-api 通路零改动。
//   KPI 带四卡 = 进行中活动 / 本月活动 / 物料备货 / 费用合计(真值;vkpi_events 无
//   历史快照 → 无时序,诚实虚线);旧页四卡口径(我参与的进行中/已花/我的待办/平均
//   ROI)零丢失 —— 收进 kpiE SrcChip 口径行 + retro 复盘模块真身。
//   联动:状态环图分段 / 类型条形点行 → 活动看板过滤(筛选状态提升到 page 层共享)。
// 红线:纯展示 + 既有 CRUD 通路,绝不触碰 viltrox_fit_score / rule_v0;颜色全 token
//   零写死色、零 opacity 修饰类;端点失败 = 诚实错误卡;布局只走本机 storageKey,
//   不传 apiToken 给 EditableDashboardBoard(其账户级持久化写死 dashboard_layout_v1
//   键,传了会覆写 Dashboard 布局 —— 金样板同注释)。

const STORAGE_KEY = "vkpi-events-board-layout-v1";

// 默认布局(12 列 · 默认简五行):kpiE(12) → board(8)+status(4) → budget(8)+upcoming(4)
// palette 备选:type / inventory / retro / team
const DEFAULT_LAYOUT = [
  { moduleKey: "kpiE", span: 12 },
  { moduleKey: "board", span: 8 },
  { moduleKey: "status", span: 4 },
  { moduleKey: "budget", span: 8 },
  { moduleKey: "upcoming", span: 4 },
];

// demo .ph-b:pagehead 药丸徽(金样板同款)
const PH_BADGE =
  "flex-none rounded-[7px] bg-accent-soft px-2 py-0.5 text-[9.5px] font-semibold tracking-[0.05em] text-accent";

// toUiEvent 输出与本地宽松 EventVm 运行时同形(旧 EventsPage 同款适配)
function asEventVm(u: UiEvent): EventVm {
  return u as unknown as EventVm;
}

interface EventsBoardPageProps {
  userName?: string;
  staff?: UiStaff[];
  currentUser?: { id?: string | number; name?: string; avatar?: string; email?: string };
  initialEventId?: string | null;
  onConsumeInitialEvent?: () => void;
}

export function EventsBoardPage({
  userName,
  staff: staffInput = [],
  currentUser: loggedInUser,
  initialEventId = null,
  onConsumeInitialEvent,
}: EventsBoardPageProps) {
  // 入口一次性归一化:CockpitApp 传 staffAdapter.UiStaff(宽松),下游全按
  // events 严格口径(name 兜底 email/id 字符串,与旧页 ownerById 解析同语义)。
  const staff = React.useMemo<EventsUiStaff[]>(
    () => staffInput.map((s) => ({ ...s, name: s.name ?? s.email ?? String(s.id) })) as unknown as EventsUiStaff[],
    [staffInput],
  );
  const { token } = useAuth();
  const [editing, setEditing] = React.useState(false);

  // 真实 staff 喂 lookups 模块缓存(渲染前同步、幂等 —— 旧 EventsPage 同款,
  // 深层旧组件 ownerById/ownerByInitial 才能解析真人)
  setRealStaff(staff);

  // 登录人 → events 口径 currentUser(EventsMockupPage 派生逻辑 1:1:优先按 id 匹配
  // 真 staff,退 email,再退首位;id 与 team_ids/ownerId 同域才对得上参与判定)
  const currentUser: CurrentUserVm = React.useMemo(() => {
    const meId = loggedInUser?.id != null ? String(loggedInUser.id) : "";
    const me =
      staff.find((s) => String(s.id) === meId) ||
      (loggedInUser?.email ? staff.find((s) => s.email === loggedInUser.email) : null) ||
      staff[0] ||
      null;
    const name = me?.name || loggedInUser?.name || userName || "成员";
    return {
      id: me ? String(me.id) : meId || "j",
      name,
      initial: (me?.avatar || name || "?").slice(0, 1).toUpperCase(),
      color: me?.color || "var(--ds-accent)",
    };
  }, [loggedInUser, staff, userName]);

  /* ---------- 数据:活动 / 项目 / 库存(旧页三效果 1:1,库存失败不再静默) ---------- */

  const [events, setEvents] = React.useState<EventVm[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState("");
  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setLoadError("");
    listEvents(token, { limit: 200 })
      .then((res) => {
        if (alive) setEvents((res.items || []).map(toUiEvent).map(asEventVm));
      })
      .catch((err: any) => {
        if (alive) {
          setEvents([]);
          setLoadError(String(err && err.message ? err.message : err));
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [token]);

  const [projectOptions, setProjectOptions] = React.useState<Array<Record<string, any>>>([]);
  React.useEffect(() => {
    let alive = true;
    apiFetch<{ projects?: Array<Record<string, any>> }>("/api/admin/vkpi/projects?limit=200", { timeoutMs: 6000 }, token)
      .then((resp) => {
        if (!alive) return;
        const items = Array.isArray(resp?.projects) ? resp.projects : [];
        setProjectOptions(items);
        setRealProjects(items);
      })
      .catch(() => {
        /* 项目选择器保持空,活动卡显 id 兜底(旧页同款静默) */
      });
    return () => {
      alive = false;
    };
  }, [token]);

  const [stock, setStock] = React.useState<StockItem[]>([]);
  const [stockLoading, setStockLoading] = React.useState(true);
  const [stockError, setStockError] = React.useState("");
  React.useEffect(() => {
    let alive = true;
    setStockLoading(true);
    setStockError("");
    listInventory(token)
      .then((res) => {
        if (alive && Array.isArray(res.items)) setStock(res.items.map(toUiStock) as StockItem[]);
      })
      .catch((err: any) => {
        if (alive) setStockError(String(err && err.message ? err.message : err));
      })
      .finally(() => {
        if (alive) setStockLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [token]);

  /* ---------- 看板筛选(page 层共享:看板控件 + 状态环图/类型条形联动同一份) ---------- */
  const [filters, setFilters] = React.useState<BoardFilters>(DEFAULT_FILTERS);
  const filtered = React.useMemo(() => filterEvents(events, filters, currentUser.id), [events, filters, currentUser.id]);
  const [listOpen, setListOpen] = React.useState(false);

  /* ---------- 详情接管 + dashboard 跳转 id 消费(旧页 1:1) ---------- */
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!initialEventId || loading) return;
    if (events.some((ev) => String(ev.id) === String(initialEventId))) {
      setSelectedId(String(initialEventId));
    }
    if (onConsumeInitialEvent) onConsumeInitialEvent();
  }, [initialEventId, loading, events, onConsumeInitialEvent]);

  const openEvent = React.useCallback((id: string) => {
    setListOpen(false);
    setSelectedId(id);
  }, []);

  /* ---------- CRUD(旧 EventsPage 处理器 1:1:乐观 + 真落库 + 失败横幅) ---------- */
  const [showNew, setShowNew] = React.useState(false);
  const [showStock, setShowStock] = React.useState(false);

  const handleCreateEvent = (data: Parameters<typeof fromUiCreate>[0]) => {
    setShowNew(false);
    createEvent(token, fromUiCreate(data))
      .then((res) => {
        const row = unwrapItem<VkpiEvent>(res);
        if (row) setEvents((prev) => [asEventVm(toUiEvent(row)), ...prev]);
      })
      .catch((err: any) => {
        setLoadError("创建失败:" + String(err && err.message ? err.message : err));
      });
  };

  const handleUpdateEvent = (updated: EventVm) => {
    setEvents((prev) => prev.map((ev) => (ev.id === updated.id ? { ...ev, ...updated } : ev))); // 乐观
    updateEvent(token, updated.id, fromUiUpdate(updated))
      .then((res) => {
        const row = unwrapItem<VkpiEvent>(res);
        if (row) setEvents((prev) => prev.map((ev) => (ev.id === row.id ? asEventVm(toUiEvent(row)) : ev)));
      })
      .catch((err: any) => {
        setLoadError("更新失败:" + String(err && err.message ? err.message : err));
      });
  };

  const handleDeleteEvent = (id: string) => {
    setSelectedId(null);
    setEvents((prev) => prev.filter((ev) => ev.id !== id)); // 乐观
    deleteEvent(token, id).catch((err: any) => {
      setLoadError("删除失败:" + String(err && err.message ? err.message : err));
    });
  };

  const handleUpdateTeam = (eventId: string, teamIds: string[]) => {
    setEvents((prev) => prev.map((ev) => (ev.id === eventId ? { ...ev, teamUserIds: teamIds } : ev))); // 乐观
    updateEvent(token, eventId, { team_ids: teamIds }) // 落库(旧页修复保留:仅本地 state 刷新即丢)
      .catch((err: any) => {
        setLoadError("团队更新失败:" + String(err && err.message ? err.message : err));
      });
  };

  const handleEventPatched = (uiRow: UiEvent) => {
    if (uiRow && uiRow.id) setEvents((prev) => prev.map((ev) => (ev.id === uiRow.id ? { ...ev, ...asEventVm(uiRow) } : ev)));
  };

  /* ---------- 卡头 props(金样板 cardProps 同构;SrcChip hover 口径卡,
     不借市场之声 GENERIC_CHAIN 溯源链 —— 链口径不同不冒充) ---------- */
  const srcOf = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] };
  const cardProps = (key: string, title: string, cnt?: React.ReactNode, extraRows?: Array<[string, string]>) => {
    const rows = extraRows && extraRows.length > 0 ? [...srcOf(key).rows, ...extraRows] : srcOf(key).rows;
    return { title, cnt, srcLabel: srcOf(key).label, srcRows: rows };
  };

  const noTokenCard = (
    <PendingCard>
      <b>未登录 / 无 token</b> —— 登录后自动加载活动数据。
    </PendingCard>
  );
  const eventsGate = (): React.ReactNode | null => {
    if (!token) return noTokenCard;
    if (loadError) return <ErrorCard title="活动列表读取失败" text={loadError} />;
    if (loading) return <div className="py-6 text-center text-[12px] text-muted">活动读取中…</div>;
    return null;
  };

  /* ---------- 模块 body ---------- */

  // 旧页四卡口径零丢失:我参与的进行中 / 我参与的已花 / 我的待办(诚实说明)/ 平均 ROI
  // → 全部收进 kpiE SrcChip 口径行(新四卡语义见 EventsKpiBand)
  const renderKpiBand = () => {
    const mine = events.filter((ev) => (ev.teamUserIds || []).includes(currentUser.id));
    const mineActive = mine.filter((ev) => (ACTIVE_STATUSES as readonly string[]).includes(ev.status));
    const mineSpent = mine.reduce((acc, ev) => acc + spentOf(ev), 0);
    const done = events.filter((ev) => ev.status === "done" && typeof ev.roi === "number");
    const avgRoi = done.length > 0 ? done.reduce((acc, ev) => acc + (ev.roi as number), 0) / done.length : null;
    const extraRows: Array<[string, string]> =
      !loading && !loadError
        ? [
            ["旧口径·我参与的进行中", `${mineActive.length} / ${mine.length} 个(team_ids 含 ${currentUser.name})`],
            ["旧口径·我参与的已花", fmtMoneyFull(mineSpent)],
            ["旧口径·平均 ROI", avgRoi != null ? `${avgRoi.toFixed(1)}x(${done.length} 个已完成)` : "已完成活动 0 个,如实无数"],
            ["跨活动待办", "逐活动详情「任务」tab 内真数(跨活动聚合需逐活动拉详情,如实不摆)"],
            ["本月活动", `${events.filter((ev) => overlapsThisMonth(ev)).length} 个`],
          ]
        : [];
    return (
      <ModuleCard {...cardProps("kpiE", "活动总览", !loading && !loadError ? `${events.length} 活动` : undefined, extraRows)}>
        {eventsGate() ?? (
          <EventsKpiBand
            events={events}
            eventsReady={!loading && !loadError}
            stockCount={stockError ? null : stock.length}
            stockPending={stockLoading || !!stockError}
            stockPendingNote={stockError ? `库存端点读取失败:${stockError}` : stockLoading ? "库存读取中…" : undefined}
          />
        )}
      </ModuleCard>
    );
  };

  const renderBoard = () => (
    <ModuleCard {...cardProps("board", "活动看板", !loading && !loadError ? `${filtered.length}/${events.length}` : undefined)}>
      {!token ? (
        noTokenCard
      ) : (
        <BoardGridBody
          events={filtered}
          totalVisible={events.length}
          staff={staff}
          loading={loading}
          error={loadError}
          filters={filters}
          onFilters={setFilters}
          onOpen={(ev) => openEvent(ev.id)}
          onShowAll={() => setListOpen(true)}
        />
      )}
    </ModuleCard>
  );

  const renderStatus = () => (
    <ModuleCard {...cardProps("status", "状态构成", !loading && !loadError ? `${events.length}` : undefined)}>
      {eventsGate() ?? (
        <StatusDonutBody
          events={events}
          onSelect={(key) => setFilters((f) => ({ ...f, status: f.status === key ? "all" : key }))}
        />
      )}
    </ModuleCard>
  );

  const renderType = () => (
    <ModuleCard {...cardProps("type", "类型分布", !loading && !loadError ? `${events.length}` : undefined)}>
      {eventsGate() ?? (
        <TypeBarsBody
          events={events}
          selectedType={filters.type === "all" ? undefined : filters.type}
          onSelect={(key) => setFilters((f) => ({ ...f, type: f.type === key ? "all" : key }))}
        />
      )}
    </ModuleCard>
  );

  const renderBudget = () => {
    const withBudget = events.filter((ev) => (Number(ev.budgetTotal) || 0) > 0 || spentOf(ev) > 0).length;
    return (
      <ModuleCard {...cardProps("budget", "预算执行", !loading && !loadError ? `${withBudget} 有账` : undefined)}>
        {eventsGate() ?? <BudgetBarsBody events={events} onOpen={openEvent} />}
      </ModuleCard>
    );
  };

  const renderUpcoming = () => (
    <ModuleCard {...cardProps("upcoming", "即将开幕", !loading && !loadError ? `${upcomingEvents(events).length}` : undefined)}>
      {eventsGate() ?? <UpcomingBody events={events} onOpen={openEvent} />}
    </ModuleCard>
  );

  const renderInventory = () => (
    <ModuleCard {...cardProps("inventory", "公司库存", !stockLoading && !stockError ? stock.length.toLocaleString() : undefined)}>
      {!token ? noTokenCard : <InventoryBody stock={stock} error={stockError} loading={stockLoading} onManage={() => setShowStock(true)} />}
    </ModuleCard>
  );

  const renderRetro = () => {
    const doneCount = events.filter((ev) => ev.status === "done").length;
    return (
      <ModuleCard {...cardProps("retro", "复盘结果", !loading && !loadError ? `${doneCount} 已完成` : undefined)}>
        {eventsGate() ?? <RetroResultsBody events={events} onOpen={openEvent} />}
      </ModuleCard>
    );
  };

  const renderTeam = () => (
    <ModuleCard {...cardProps("team", "团队参与", !loading && !loadError ? `${staff.length} 人` : undefined)}>
      {eventsGate() ?? <TeamLoadBody events={events} staff={staff} />}
    </ModuleCard>
  );

  /* ---------- 模块注册表(palette 全量可选;默认简五卡,type/inventory/retro/team 备选) ---------- */
  const modules: DashboardModuleDefinition[] = [
    { key: "kpiE", label: "活动总览带", description: "进行中 / 本月 / 物料备货 / 费用合计(全真值)", category: "核心模块", defaultSpan: 12, minSpan: 6, defaultHeight: 6, minHeight: 4, maxHeight: 12, render: renderKpiBand },
    { key: "board", label: "活动看板", description: "活动卡片 + 我参与的/状态/类型/搜索过滤 · 点卡进详情", category: "核心模块", defaultSpan: 8, minSpan: 4, defaultHeight: 18, minHeight: 8, maxHeight: 36, render: renderBoard },
    { key: "status", label: "状态构成", description: "六状态环图 · 分段点击联动看板过滤", category: "核心模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 5, maxHeight: 16, render: renderStatus },
    { key: "budget", label: "预算执行", description: "每活动 spent/plan 条形 · 超支/近超语义染色", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 20, render: renderBudget },
    { key: "upcoming", label: "即将开幕", description: "end_date ≥ 今天升序 · 真实倒计时", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 20, render: renderUpcoming },
    // ↓ palette 备选(不进默认布局,注册表保留全量可选)
    { key: "type", label: "类型分布", description: "活动类型条形 · 点行联动看板过滤", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderType },
    { key: "inventory", label: "公司库存", description: "vkpi_inventory 概览 + 管理弹窗入口(跨活动共享)", category: "实时模块", defaultSpan: 4, minSpan: 3, defaultHeight: 8, minHeight: 4, maxHeight: 20, render: renderInventory },
    { key: "retro", label: "复盘结果", description: "已完成活动 ROI/线索/视频 + 平均 ROI", category: "业务板块", defaultSpan: 8, minSpan: 4, defaultHeight: 7, minHeight: 4, maxHeight: 20, render: renderRetro },
    { key: "team", label: "团队参与", description: "team_ids × 员工名单,每人参与活动数", category: "业务板块", defaultSpan: 4, minSpan: 3, defaultHeight: 7, minHeight: 4, maxHeight: 16, render: renderTeam },
  ];

  /* ---------- 详情接管(旧页 selectedId 早退 1:1;七 tab 旧件零改动) ---------- */
  const selected = selectedId ? events.find((ev) => String(ev.id) === String(selectedId)) : null;
  if (selectedId && !selected) {
    // 乐观删除/列表刷新后失联 → 回看板(旧页同款自愈)
    setSelectedId(null);
    return null;
  }
  if (selected) {
    return (
      <div className="p-4 md:px-[22px] md:py-[15px]">
        <EventDetailTakeover
          ev={selected}
          currentUser={currentUser}
          staff={staff}
          token={token}
          stock={stock}
          setStock={setStock}
          projectOptions={projectOptions}
          onBack={() => setSelectedId(null)}
          onUpdateEvent={handleUpdateEvent}
          onDeleteEvent={handleDeleteEvent}
          onUpdateTeam={(teamIds) => handleUpdateTeam(selected.id, teamIds)}
          onEventPatched={handleEventPatched}
        />
      </div>
    );
  }

  return (
    <div className="p-4 md:px-[22px] md:py-[15px]">
      {/* pagehead(demo 范式):标题 + 药丸徽 + 实时辉光点 + 公司库存 / 新建 / 编辑布局 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-[18px] font-[680] tracking-[-0.02em] text-ink">Events · 市场活动</span>
          {!loading && !loadError && <span className={PH_BADGE}>{events.length} 活动</span>}
          <span className={PH_BADGE}>可编辑看板</span>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mr-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted">
            <span className="h-[5px] w-[5px] rounded-full bg-good" style={{ boxShadow: "0 0 var(--ds-glow-radius, 0px) var(--ds-good)" }} />
            实时
          </span>
          <button
            type="button"
            onClick={() => setShowStock(true)}
            title="管理公司库存(跨活动共享)"
            className="flex items-center gap-1.5 rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <Package size={13} />
            <span>公司库存{!stockLoading && !stockError ? `(${stock.length})` : ""}</span>
          </button>
          <button
            type="button"
            onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 rounded-xl border border-accent bg-accent-soft px-3 py-2 text-[12px] font-medium text-accent transition-colors hover:border-accent-hover"
          >
            <Plus size={13} />
            <span>新建 Event</span>
          </button>
          <button
            type="button"
            onClick={() => setEditing((value) => !value)}
            aria-pressed={editing}
            className={`vkpi-layout-edit-button flex items-center gap-1.5 ${editing ? "is-active" : ""}`}
          >
            <PencilLine size={13} />
            <span>{editing ? "完成布局" : "编辑布局"}</span>
          </button>
        </div>
      </div>

      {!token && <div className="mb-3">{noTokenCard}</div>}
      {loadError && (
        <div className="mb-3">
          <ErrorCard title="活动列表读取失败" text={loadError} />
        </div>
      )}

      {/* 可编辑看板:布局本机记忆(storageKey);不传 apiToken,见文件头红线注释 */}
      <EditableDashboardBoard modules={modules} defaultLayout={DEFAULT_LAYOUT} editing={editing} storageKey={STORAGE_KEY} />

      {/* 活动全量弹窗(卡面 6 张之外的全量;点卡直接进详情) */}
      {listOpen && <EventsListDialog events={filtered} staff={staff} onOpen={(ev) => openEvent(ev.id)} onClose={() => setListOpen(false)} />}

      {/* 看板层旧弹窗(新建活动 / 公司库存)—— embeds 透传,旧组件零改动 */}
      <EventsBoardModals
        showNew={showNew}
        onCloseNew={() => setShowNew(false)}
        onCreate={handleCreateEvent}
        staff={staff}
        currentUserId={currentUser.id}
        projectOptions={projectOptions}
        showStock={showStock}
        onCloseStock={() => setShowStock(false)}
        stock={stock}
        setStock={setStock}
        token={token}
      />
    </div>
  );
}

// ── CockpitApp 切换(报告用,一行;本刀不改 CockpitApp)──────────────────────────
//   CockpitApp.tsx L74 的 lazy import 改为:
//   const EventsMockupPage = React.lazy(() => import("./pages/EventsBoardPage").then((m) => ({ default: m.EventsBoardPage })));
//   props 完全同形(userName/staff/currentUser/initialEventId/onConsumeInitialEvent);
//   旧页族已退役于 4bcc5c68c 后收尾波(2026-07-12),回滚需从 git 历史找回。
