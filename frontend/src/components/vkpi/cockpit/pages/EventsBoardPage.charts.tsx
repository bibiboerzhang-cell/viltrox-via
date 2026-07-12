import React from "react";
import { EmptyLine, KpiCard, type Row } from "./MarketVoicePage.modules";
import { BarRow, CatDonutBody } from "./MarketVoicePage.charts";
import { EVENT_STATUS, EVENT_TYPES } from "../../pages/events/shared/constants";
import { fmtMoneyShort, sum } from "../../pages/events/shared/helpers";
import type { EventVm, UiStaff } from "../../pages/events/shared/types";

// Events · 板块页范式图表族(EventsBoardPage 专用,页内拆件不入公共桶;
//   金样板 = MarketVoicePage.charts / MyKolBoardPage.charts 同构,图形件全复用:
//   KpiCard(demo .kpi)/ CatDonutBody(demo donut)/ BarRow(demo mplatrow)零自造样式)。
//   数据 = 页层已拉取的真活动行(GET /api/admin/vkpi/events → vkpi_events)与真库存行
//   (GET /api/admin/vkpi/inventory → vkpi_inventory),本文件纯组合零网络。
//   KPI 四卡:进行中活动 / 本月活动 / 物料备货 / 费用合计 —— 全真值;vkpi_events 无
//   历史快照表 → 四卡无时序,KpiCard 渲染 demo .spempty 纯虚线(诚实无 sparkline,
//   绝不编序列);环比同理诚实省略药丸。
//   倒计时用真实「今天」(new Date());events family helpers.TODAY 的 mock 定格
//   2026-05-26 债已在收尾波(2026-07-12)清除,helpers.TODAY 现同为真实当前时间。
// 红线:纯展示零网络;不触 viltrox_fit_score / rule_v0;颜色全 token(状态/类型的
//   旧 hex 常量不进新图形,统一走 STATUS_TONE token 类);零 opacity 修饰类;
//   诚实空态(零预算/零已完成如实说,不摆假图)。

/* ============ 口径工具(真实「今天」;绝不复用 helpers.TODAY mock 定格) ============ */

// 进行中管线口径 = 未完结三态(旧页 KPI「进行中」同口径,SrcChip 记档)
export const ACTIVE_STATUSES = ["planning", "prep_ready", "live"] as const;

/** 距开幕天数(真实今天,按浏览器本地日;正=倒计时,负=已开幕)。 */
export function realDaysUntil(dateStr: string): number {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return 0;
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

/** 活动起止区间与本月(浏览器时区)是否重叠 —— KPI「本月活动」口径。 */
export function overlapsThisMonth(ev: EventVm, now = new Date()): boolean {
  const start = new Date(ev.startDate);
  const end = new Date(ev.endDate || ev.startDate);
  if (Number.isNaN(start.getTime())) return false;
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const monthEnd = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59, 59);
  const safeEnd = Number.isNaN(end.getTime()) ? start : end;
  return start.getTime() <= monthEnd.getTime() && safeEnd.getTime() >= monthStart.getTime();
}

/** 全额美元(KPI 大数用;缩写版 fmtMoneyShort 留给行内)。 */
export const fmtMoneyFull = (n: number): string => "$" + Math.round(n).toLocaleString();

/** 单活动已花(budget_json 各分类 spent 合计;旧页同口径)。 */
export const spentOf = (ev: EventVm): number => sum(ev.budgetByCategory || {}, "spent");

// 状态 → token 语义色(新图形零旧 hex;与 EVENT_STATUS.label 一一对应)
export const STATUS_TONE: Record<string, string> = {
  planning: "border-info bg-info-soft text-info",
  prep_ready: "border-accent-2 text-accent-2",
  live: "border-warn bg-warn-soft text-warn",
  wrap_up: "border-line bg-card text-ink-2",
  done: "border-good bg-good-soft text-good",
  cancelled: "border-line text-muted",
};

export const statusLabel = (key: string): string => EVENT_STATUS[key]?.label || key || "未知";
export const typeLabel = (key: string): string => EVENT_TYPES[key]?.label || key || "未知";

/* ============ KPI 带四卡(demo .kpi;真值 + 无时序诚实虚线) ============ */

export function EventsKpiBand({
  events,
  eventsReady,
  stockCount,
  stockPending,
  stockPendingNote,
}: {
  events: EventVm[];
  /** 活动列表是否已真实到货(未到货 → 前两卡 + 费用卡 pending,不摆 0 冒充) */
  eventsReady: boolean;
  /** 库存件数(vkpi_inventory 行数);null = 端点未到货/失败 → 诚实 pending */
  stockCount: number | null;
  stockPending?: boolean;
  stockPendingNote?: string;
}) {
  const active = events.filter((ev) => (ACTIVE_STATUSES as readonly string[]).includes(ev.status));
  const thisMonth = events.filter((ev) => overlapsThisMonth(ev));
  const spentTotal = events.reduce((acc, ev) => acc + spentOf(ev), 0);
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {eventsReady ? (
        <KpiCard label="进行中活动" value={active.length.toLocaleString()} unit="个" seriesColor="var(--ds-accent)" />
      ) : (
        <KpiCard label="进行中活动" value="—" pending pendingNote="活动列表待到货" />
      )}
      {eventsReady ? (
        <KpiCard label="本月活动" value={thisMonth.length.toLocaleString()} unit="个" seriesColor="var(--ds-accent-2)" />
      ) : (
        <KpiCard label="本月活动" value="—" pending pendingNote="活动列表待到货" />
      )}
      {stockCount != null && !stockPending ? (
        <KpiCard label="物料备货" value={stockCount.toLocaleString()} unit="件" seriesColor="var(--ds-good)" />
      ) : (
        <KpiCard label="物料备货" value="—" pending pendingNote={stockPendingNote || "库存端点待到货"} />
      )}
      {eventsReady ? (
        <KpiCard label="费用合计" value={fmtMoneyFull(spentTotal)} seriesColor="var(--ds-warn)" tone={spentTotal > 0 ? "warn" : "good"} />
      ) : (
        <KpiCard label="费用合计" value="—" pending pendingNote="活动列表待到货" />
      )}
    </div>
  );
}

/* ============ 状态构成环图(CatDonutBody 复用:分段可点 → 看板状态过滤) ============ */

export function StatusDonutBody({
  events,
  onSelect,
}: {
  events: EventVm[];
  /** 分段/图例行点击 → 页层看板过滤联动(key=状态 key) */
  onSelect?: (key: string, label: string, count: number) => void;
}) {
  const counts = new Map<string, number>();
  events.forEach((ev) => counts.set(ev.status, (counts.get(ev.status) || 0) + 1));
  const categories: Row[] = [...counts.entries()].map(([key, count]) => ({ key, label: statusLabel(key), count }));
  if (events.length === 0) return <EmptyLine text="暂无活动,状态环图诚实不画。" />;
  return <CatDonutBody categories={categories} totalMatched={events.length} onSelect={onSelect} />;
}

/* ============ 类型分布条形(BarRow 复用:点行 → 看板类型过滤) ============ */

export function TypeBarsBody({
  events,
  selectedType,
  onSelect,
}: {
  events: EventVm[];
  selectedType?: string;
  onSelect?: (typeKey: string) => void;
}) {
  if (events.length === 0) return <EmptyLine text="暂无活动,类型分布诚实不画。" />;
  const counts = new Map<string, number>();
  events.forEach((ev) => counts.set(ev.typeKey || "other", (counts.get(ev.typeKey || "other") || 0) + 1));
  const rows = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const max = rows.reduce((acc, [, n]) => Math.max(acc, n), 0);
  return (
    <div>
      {rows.map(([key, count]) => (
        <BarRow
          key={key}
          name={typeLabel(key)}
          widthPct={max > 0 ? (count / max) * 100 : 0}
          value={`×${count}`}
          highlight={selectedType === key}
          title={onSelect ? "点击按此类型过滤活动看板" : undefined}
          onClick={onSelect ? () => onSelect(key) : undefined}
        />
      ))}
      <div className="mt-[7px] font-mono text-[9px] text-muted">vkpi_events.type_key · 点行联动活动看板过滤</div>
    </div>
  );
}

/* ============ 预算执行条形(每活动一行:spent/plan;超支 crit / 近超 warn) ============ */

export function BudgetBarsBody({ events, onOpen }: { events: EventVm[]; onOpen?: (id: string) => void }) {
  if (events.length === 0) return <EmptyLine text="暂无活动,预算执行诚实不画。" />;
  const rows = [...events].sort((a, b) => spentOf(b) - spentOf(a));
  return (
    <div>
      {rows.map((ev) => {
        const spent = spentOf(ev);
        const total = Number(ev.budgetTotal) || 0;
        const pct = total > 0 ? Math.round((spent / total) * 100) : 0;
        const color =
          total > 0 && pct > 100
            ? "linear-gradient(90deg, var(--ds-crit), var(--ds-crit))"
            : total > 0 && pct > 80
              ? "linear-gradient(90deg, var(--ds-warn), var(--ds-warn))"
              : undefined;
        return (
          <BarRow
            key={ev.id}
            name={ev.title || "未命名活动"}
            widthPct={total > 0 ? Math.min(100, pct) : 0}
            color={color}
            dashed={total === 0}
            value={total > 0 ? `${fmtMoneyShort(spent)} / ${fmtMoneyShort(total)} · ${pct}%` : `${fmtMoneyShort(spent)} · 未设预算`}
            title={onOpen ? "点击打开该活动详情" : undefined}
            onClick={onOpen ? () => onOpen(ev.id) : undefined}
          />
        );
      })}
      <div className="mt-[7px] font-mono text-[9px] text-muted">
        budget_json 各分类 spent/plan 合计 · 逐笔费用在活动详情(vkpi_event_expenses)
      </div>
    </div>
  );
}

/* ============ 团队参与条形(team_ids jsonb × 真 staff 名单) ============ */

export function TeamLoadBody({ events, staff }: { events: EventVm[]; staff: UiStaff[] }) {
  if (events.length === 0) return <EmptyLine text="暂无活动,团队参与诚实不画。" />;
  if (!Array.isArray(staff) || staff.length === 0) return <EmptyLine text="员工名单未加载,无法解析 team_ids。" />;
  const rows = staff
    .map((member) => ({
      name: member.name,
      count: events.filter((ev) => (ev.teamUserIds || []).includes(String(member.id))).length,
    }))
    .filter((row) => row.count > 0)
    .sort((a, b) => b.count - a.count);
  if (rows.length === 0) return <EmptyLine text="现有活动的 team_ids 均未命中员工名单。" />;
  const max = rows.reduce((acc, row) => Math.max(acc, row.count), 0);
  return (
    <div>
      {rows.map((row) => (
        <BarRow key={row.name} name={row.name} widthPct={max > 0 ? (row.count / max) * 100 : 0} value={`×${row.count}`} />
      ))}
      <div className="mt-[7px] font-mono text-[9px] text-muted">vkpi_events.team_ids(jsonb,staff.id)× 员工名单</div>
    </div>
  );
}
