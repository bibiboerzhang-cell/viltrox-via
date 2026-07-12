import React from "react";
import EventCard from "../../pages/events/components/EventCard";
import { EVENT_STATUS, EVENT_TYPES } from "../../pages/events/shared/constants";
import type { EventVm, StockItem, UiStaff } from "../../pages/events/shared/types";
import { EmptyLine, ErrorCard } from "./MarketVoicePage.modules";
import { ModalShell } from "./MarketVoicePage.dialogs";
import { BarRow } from "./MarketVoicePage.charts";
import { STATUS_TONE, realDaysUntil, statusLabel } from "./EventsBoardPage.charts";

// Events · 板块页范式辅助件(EventsBoardPage 专用,页内拆件不入公共桶)。
//   MODULE_SOURCES = 每模块 SrcChip 口径(label=真实表名,rows=口径行;金样板同构,
//   卡面零术语——表名/权限口径全部住 SrcChip hover 卡,不上卡面)。
//   看板模块 = 旧 EventsPage 的筛选条 + EventCard 栅格收编:EventCard 旧组件零改动
//   直接复用(硬编码 slate/hex 由 cockpit-reference.css 通用换肤层按 token 重映射);
//   卡面收敛 6 张(demo FULL.slice(0,6)),全量走 EventsListDialog(ModalShell 复用)。
//   即将开幕 = 同一份真活动行的 end_date ≥ 今天视图(真实今天,非 helpers.TODAY mock)。
// 红线:本文件零直连网络(数据/动作全走 page 层);不触 viltrox_fit_score / rule_v0;
//   新代码颜色全 token 零写死色、零 opacity 修饰类;诚实空态。

/* ============ SrcChip 口径注册表(真实表名;溯源弹窗不借市场之声 GENERIC_CHAIN,
   链口径不同不冒充 —— MyKolBoardPage 同款裁决:hover 口径卡起步) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiE: {
    label: "vkpi_events · vkpi_inventory",
    rows: [
      ["活动表", "vkpi_events(GET /api/admin/vkpi/events)"],
      ["可见范围", "管理层全量;员工 = owner / team_ids(jsonb)/ 显式共享(vkpi_event_members)/ 公开活动(服务端裁剪)"],
      ["进行中口径", "status ∈ 筹备中 / 物料就绪 / 进行中(未完结管线)"],
      ["本月口径", "起止日与本月重叠(浏览器时区)"],
      ["物料备货", "vkpi_inventory 库存条目数(公司库存,跨活动共享)"],
      ["费用合计", "budget_json 各分类 spent 合计;逐笔明细 vkpi_event_expenses(活动详情「预算+费用」)"],
      ["时序", "vkpi_events 无历史快照表 → 四卡无趋势线,如实虚线不编序列"],
    ],
  },
  board: {
    label: "vkpi_events",
    rows: [
      ["主表", "vkpi_events(排序 start_date DESC)"],
      ["权限", "「我参与的」= team_ids(jsonb)含当前登录人;全量由服务端按身份裁剪"],
      ["详情子表", "vkpi_event_tasks / vkpi_event_expenses / vkpi_event_kol_invites / vkpi_event_materials / vkpi_event_products"],
      ["卡面", "收敛 6 张 · 全量走弹窗;点卡进详情(概览/预算+费用/任务/KOL/物料/复盘)"],
    ],
  },
  status: {
    label: "vkpi_events.status",
    rows: [
      ["口径", "六状态字典:筹备中 / 物料就绪 / 进行中 / 复盘中 / 已完成 / 已取消"],
      ["联动", "分段与图例行可点 → 活动看板按状态过滤"],
    ],
  },
  type: {
    label: "vkpi_events.type_key",
    rows: [
      ["口径", "活动类型字典(展会/媒体/Webinar/KOL 聚会/团建/其他)"],
      ["联动", "点行 → 活动看板按类型过滤"],
    ],
  },
  budget: {
    label: "vkpi_events.budget_json",
    rows: [
      ["口径", "每活动 spent / plan(budget_json 分类合计)"],
      ["染色", ">100% 超支 crit · >80% 近超 warn · 未设预算虚线弱化"],
      ["口径差", "逐笔费用落 vkpi_event_expenses,不自动回写 budget_json —— 两账如实分列"],
    ],
  },
  upcoming: {
    label: "vkpi_events.start_date",
    rows: [
      ["口径", "end_date ≥ 今天(浏览器本地日),按开幕日升序"],
      ["倒计时", "真实今天计算(非定格 mock 日期)"],
    ],
  },
  inventory: {
    label: "vkpi_inventory",
    rows: [
      ["主表", "vkpi_inventory(sku 唯一;is_sample 标样品)"],
      ["流水", "vkpi_inventory_movements(每次增删改/调量落审计行)"],
      ["分组", "vkpi_inventory_groups + vkpi_inventory_group_items(按活动打包)"],
      ["管理", "「公司库存」弹窗 = 全量 CRUD + 调动记录 + 分组(跨活动共享)"],
    ],
  },
  retro: {
    label: "vkpi_events · vkpi_event_retrospectives",
    rows: [
      ["结果列", "vkpi_events.roi / leads / videos / retrospective(详情「复盘」tab 落库)"],
      ["快照", "vkpi_event_retrospectives(「定格复盘」逐次快照)"],
      ["口径", "已完成(done)活动的结果行;平均 ROI = done 活动 roi 均值"],
    ],
  },
  team: {
    label: "vkpi_events.team_ids",
    rows: [
      ["口径", "team_ids(jsonb,staff.id)× 员工名单,计每人参与活动数"],
      ["身份", "与详情页参与判定同一口径(非参与者详情只读拦截)"],
    ],
  },
};

/* ============ 看板筛选(旧页筛选条零丢失:我参与的/全部 + 状态 + 类型 + 搜索) ============ */

export interface BoardFilters {
  mine: boolean;
  status: string; // "all" | EVENT_STATUS key
  type: string; // "all" | EVENT_TYPES key
  query: string;
}

export const DEFAULT_FILTERS: BoardFilters = { mine: false, status: "all", type: "all", query: "" };

export function filterEvents(events: EventVm[], filters: BoardFilters, meId: string): EventVm[] {
  return events.filter((ev) => {
    if (filters.mine && !(ev.teamUserIds || []).includes(meId)) return false;
    if (filters.status !== "all" && ev.status !== filters.status) return false;
    if (filters.type !== "all" && ev.typeKey !== filters.type) return false;
    if (filters.query) {
      const q = filters.query.toLowerCase();
      const hitTitle = (ev.title || "").toLowerCase().includes(q);
      const hitCity = (ev.location?.city || "").toLowerCase().includes(q);
      if (!hitTitle && !hitCity) return false;
    }
    return true;
  });
}

const INPUT_CLS = "rounded-xl border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none focus:border-accent";
const CHIP_CLS = (on: boolean) =>
  `rounded-full border px-2.5 py-1 text-[10.5px] transition-colors ${on ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-ink"}`;

export function BoardControls({ filters, onChange }: { filters: BoardFilters; onChange: (next: BoardFilters) => void }) {
  return (
    <div className="mb-2.5 flex flex-wrap items-center gap-2">
      <button type="button" className={CHIP_CLS(filters.mine)} onClick={() => onChange({ ...filters, mine: true })}>
        我参与的
      </button>
      <button type="button" className={CHIP_CLS(!filters.mine)} onClick={() => onChange({ ...filters, mine: false })}>
        全部
      </button>
      <input
        type="text"
        value={filters.query}
        onChange={(ev) => onChange({ ...filters, query: ev.target.value })}
        placeholder="搜索活动 / 城市"
        className={`${INPUT_CLS} w-40`}
      />
      <select value={filters.status} onChange={(ev) => onChange({ ...filters, status: ev.target.value })} className={INPUT_CLS}>
        <option value="all">全部状态</option>
        {Object.entries(EVENT_STATUS).map(([key, cfg]) => (
          <option key={key} value={key}>
            {cfg.label}
          </option>
        ))}
      </select>
      <select value={filters.type} onChange={(ev) => onChange({ ...filters, type: ev.target.value })} className={INPUT_CLS}>
        <option value="all">全部类型</option>
        {Object.entries(EVENT_TYPES).map(([key, cfg]) => (
          <option key={key} value={key}>
            {cfg.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/* ============ 活动看板 body(EventCard 旧件复用;卡面 6 张 + 全量弹窗入口) ============ */

const BOARD_FACE = 6; // demo FULL.slice(0,6) 同构

export function BoardGridBody({
  events,
  totalVisible,
  staff,
  loading,
  error,
  filters,
  onFilters,
  onOpen,
  onShowAll,
}: {
  /** 已过滤活动(页层 filterEvents 产物) */
  events: EventVm[];
  /** 未过滤可见总数(空态区分「没活动」vs「筛选无命中」) */
  totalVisible: number;
  staff: UiStaff[];
  loading: boolean;
  error: string;
  filters: BoardFilters;
  onFilters: (next: BoardFilters) => void;
  onOpen: (ev: EventVm) => void;
  onShowAll: () => void;
}) {
  if (error) return <ErrorCard title="活动列表读取失败" text={error} />;
  if (loading) return <div className="py-6 text-center text-[12px] text-muted">活动读取中…</div>;
  return (
    <div>
      <BoardControls filters={filters} onChange={onFilters} />
      {totalVisible === 0 ? (
        <EmptyLine text="暂无活动 —— 点右上「新建 Event」创建第一个。" />
      ) : events.length === 0 ? (
        <EmptyLine text="没有匹配的活动(试试放宽状态/类型/搜索条件)。" />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {events.slice(0, BOARD_FACE).map((ev) => (
            <EventCard key={ev.id} ev={ev} staff={staff} onOpen={onOpen} />
          ))}
        </div>
      )}
      {events.length > BOARD_FACE && (
        <button
          type="button"
          onClick={onShowAll}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量 {events.length} 个活动
        </button>
      )}
    </div>
  );
}

/** 全量活动弹窗(ModalShell 复用;网格三列,点卡直接进详情)。 */
export function EventsListDialog({
  events,
  staff,
  onOpen,
  onClose,
}: {
  events: EventVm[];
  staff: UiStaff[];
  onOpen: (ev: EventVm) => void;
  onClose: () => void;
}) {
  return (
    <ModalShell title="活动全量" sub={`${events.length} 个(当前筛选口径)· vkpi_events`} onClose={onClose} maxWidth="max-w-[1080px]">
      {events.length === 0 ? (
        <EmptyLine text="当前筛选无命中。" />
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {events.map((ev) => (
            <EventCard key={ev.id} ev={ev} staff={staff} onOpen={onOpen} />
          ))}
        </div>
      )}
    </ModalShell>
  );
}

/* ============ 即将开幕(end_date ≥ 今天,升序;真实今天倒计时) ============ */

/** 未来窗口内活动(body 与卡头 cnt 共用同一口径,徽数=真实行数)。 */
export function upcomingEvents(events: EventVm[]): EventVm[] {
  return events
    .filter((ev) => realDaysUntil(ev.endDate || ev.startDate) >= 0 && ev.status !== "cancelled" && ev.status !== "done")
    .sort((a, b) => String(a.startDate).localeCompare(String(b.startDate)));
}

export function UpcomingBody({ events, onOpen }: { events: EventVm[]; onOpen: (id: string) => void }) {
  const rows = upcomingEvents(events);
  if (rows.length === 0) return <EmptyLine text="没有未来窗口内的活动(end_date ≥ 今天为空)。" />;
  return (
    <div>
      {rows.map((ev) => {
        const days = realDaysUntil(ev.startDate);
        const tone = STATUS_TONE[ev.status] || "border-line text-muted";
        return (
          <div
            key={ev.id}
            role="button"
            tabIndex={0}
            onClick={() => onOpen(ev.id)}
            onKeyDown={(kev) => {
              if (kev.key === "Enter" || kev.key === " ") {
                kev.preventDefault();
                onOpen(ev.id);
              }
            }}
            title="点击打开该活动详情"
            className="flex cursor-pointer items-center gap-2 border-b border-line py-2 text-[11.5px] transition-colors last:border-0 hover:text-accent"
          >
            <span className="w-[74px] flex-none font-mono text-[10px] text-muted">
              {String(ev.startDate).slice(5)}
              {ev.endDate && ev.endDate !== ev.startDate ? ` → ${String(ev.endDate).slice(5)}` : ""}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink-2">{ev.title || "未命名活动"}</span>
            <span className={`flex-none rounded-[5px] border px-1.5 py-px text-[9px] font-semibold ${tone}`}>{statusLabel(ev.status)}</span>
            <span className={`w-[76px] flex-none text-right font-mono text-[9.5px] ${days <= 14 ? "text-warn" : "text-muted"}`}>
              {days > 0 ? `${days} 天后` : days === 0 ? "今天开幕" : `进行中 ${-days} 天`}
            </span>
          </div>
        );
      })}
      <div className="mt-[7px] font-mono text-[9px] text-muted">end_date ≥ 今天 · 倒计时按浏览器本地日</div>
    </div>
  );
}

/* ============ 复盘结果(done 活动结果行 + 平均 ROI;0 行诚实空) ============ */

export function RetroResultsBody({ events, onOpen }: { events: EventVm[]; onOpen: (id: string) => void }) {
  const done = events.filter((ev) => ev.status === "done");
  if (done.length === 0) return <EmptyLine text="已完成活动 0 个 —— 复盘结果落库后自动点亮(详情「复盘」tab 保存)。" />;
  const withRoi = done.filter((ev) => typeof ev.roi === "number" && Number.isFinite(ev.roi));
  const avgRoi = withRoi.length > 0 ? withRoi.reduce((acc, ev) => acc + (ev.roi as number), 0) / withRoi.length : null;
  return (
    <div>
      <div className="mb-2 flex items-baseline gap-2">
        <span className="font-mono text-[22px] font-bold text-ink">{avgRoi != null ? `${avgRoi.toFixed(1)}x` : "—"}</span>
        <span className="text-[10.5px] text-muted">
          平均 ROI · {withRoi.length}/{done.length} 个已完成活动有 ROI 数
        </span>
      </div>
      {done.map((ev) => (
        <div
          key={ev.id}
          role="button"
          tabIndex={0}
          onClick={() => onOpen(ev.id)}
          onKeyDown={(kev) => {
            if (kev.key === "Enter" || kev.key === " ") {
              kev.preventDefault();
              onOpen(ev.id);
            }
          }}
          title="点击打开该活动复盘"
          className="flex cursor-pointer items-center gap-2 border-b border-line py-2 text-[11.5px] transition-colors last:border-0 hover:text-accent"
        >
          <span className="min-w-0 flex-1 truncate text-ink-2">{ev.title || "未命名活动"}</span>
          <span className="flex-none font-mono text-[10px] text-muted">
            ROI {typeof ev.roi === "number" ? `${ev.roi}x` : "—"} · 线索 {typeof ev.leads === "number" ? ev.leads : "—"} · 视频{" "}
            {typeof ev.videos === "number" ? String(ev.videos) : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ============ 公司库存概览(vkpi_inventory 真行;管理入口 = 旧 StockManagerModal) ============ */

export function InventoryBody({
  stock,
  error,
  loading,
  onManage,
}: {
  stock: StockItem[];
  error: string;
  loading: boolean;
  onManage: () => void;
}) {
  if (error) return <ErrorCard title="库存端点读取失败" text={error} />;
  if (loading) return <div className="py-6 text-center text-[12px] text-muted">库存读取中…</div>;
  const totalQty = stock.reduce((acc, item) => acc + (Number(item.qty) || 0), 0);
  const sampleCount = stock.filter((item) => item.isSample).length;
  const byCategory = new Map<string, number>();
  stock.forEach((item) => byCategory.set(item.category || "other", (byCategory.get(item.category || "other") || 0) + 1));
  const catRows = [...byCategory.entries()].sort((a, b) => b[1] - a[1]);
  const max = catRows.reduce((acc, [, n]) => Math.max(acc, n), 0);
  const CAT_LABEL: Record<string, string> = { lens: "镜头", equipment: "设备", accessory: "配件" };
  return (
    <div>
      {stock.length === 0 ? (
        <EmptyLine text="公司库存 0 条(vkpi_inventory 空表)。" />
      ) : (
        <div>
          <div className="mb-2 flex items-baseline gap-2">
            <span className="font-mono text-[22px] font-bold text-ink">{stock.length.toLocaleString()}</span>
            <span className="text-[10.5px] text-muted">
              条目 · 合计 {totalQty.toLocaleString()} 件 · 样品 {sampleCount} 条
            </span>
          </div>
          {catRows.map(([key, count]) => (
            <BarRow key={key} name={CAT_LABEL[key] || key} widthPct={max > 0 ? (count / max) * 100 : 0} value={`×${count}`} />
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={onManage}
        className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
      >
        ⧉ 管理公司库存(增删改 / 调量 / 分组 / 调动记录)
      </button>
    </div>
  );
}
