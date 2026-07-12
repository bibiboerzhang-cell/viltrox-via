import React from "react";
import type { VkpiProjectRow, VkpiProjectStage } from "../../vkpiTypes";
import { EmptyLine, KpiCard, type Row } from "./MarketVoicePage.modules";
import { BarRow } from "./MarketVoicePage.charts";
import { platformBadge } from "./MarketVoicePage.dialogs";
import { formatLocal } from "../../lib/timeLocal";
import {
  ATTRIBUTION_CONFIDENCE,
  WINDOW_STATUS,
  centsToUsd,
  fmtUsd2,
} from "../../../../services/vkpi/projectsBoard-api";
import {
  boardSeriesDelta,
  boardSeriesVals,
  type VkpiBoardSeriesResponse,
} from "../../../../services/vkpi/boardSeries-api";

// Projects 板块页 · 图形件(金样板 MarketVoicePage.charts / LaunchPadBoardPage.ops 同构)。
//   ProjectsKpiBand   四卡真值:进行中项目 / 在项 KOL / 本窗发布(30天) / 归因销售(USD)。
//                     趋势线 = board-series?board=projects 按日真序列(挂账迸发①):
//                     进行中项目←projects_new(新建项目/日,关联指标)、在项 KOL←
//                     stage_advances(阶段推进事件/日,关联指标)、本窗发布←
//                     content_posted(与卡面大数同口径 30 天窗 → 环比药丸同源渲染)、
//                     归因销售←attribution_revenue_cents(按日金额,美分序列画形状)。
//                     关联指标卡不挂环比药丸(卡面大数是存量,拿流量环比冒充是编数);
//                     端点失败 / 单指标降级 → boardSeriesVals=null → spempty 诚实虚线。
//   分组口径          buildCampaignGroupsLite = 旧 ProjectCampaignBoard 同一状态分桶
//                     (paused/取消/终态/收尾/规划/进行,零第二口径),KPI 与卡片墙对得上。
//   WindowsBody       观察窗口行(开窗环节):状态徽 + 项目 + 窗口起止 + 扫描数,点行详情。
//   AttributionBody   归因销售行:平台徽 + 订单/引用 + 置信徽 + 金额(美分→美元换算带单测)。
//   CostsBody         成本概览:项目组 Σ cost 条形 Top(null=无账本链路如实跳过)。
// 红线:本文件零直连网络;不触 viltrox_fit_score / rule_v0;颜色全 token 零写死色;
//   禁 opacity 修饰类;时间一律绝对时间戳(存 UTC 按浏览器时区,formatLocal)。

export const FACE_ROWS = 6; // demo FULL.slice(0,6):卡面收敛条数,全量走弹窗

const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    fn();
  }
};

/* ============ 项目状态分桶(旧 ProjectCampaignBoard 同口径,KPI 与卡片墙一致) ============ */

const terminalStages = new Set<VkpiProjectStage>(["closed", "released"]);
const cancelledStages = new Set<VkpiProjectStage>(["cancelled", "lost", "stalled"]);
const planningStages = new Set<VkpiProjectStage>(["invited", "discovery"]);
const wrappingStages = new Set<VkpiProjectStage>(["content_published", "published", "measured"]);

export type CampaignStatus = "规划中" | "进行中" | "收尾中" | "已暂停" | "已结束" | "已取消";

export function campaignStatusOf(project: VkpiProjectRow): CampaignStatus {
  if (project.followStatus === "paused") return "已暂停";
  if (cancelledStages.has(project.stage)) return "已取消";
  if (terminalStages.has(project.stage)) return "已结束";
  if (wrappingStages.has(project.stage)) return "收尾中";
  if (planningStages.has(project.stage)) return "规划中";
  return "进行中";
}

function groupStatusOf(rows: VkpiProjectRow[]): CampaignStatus {
  const statuses = rows.map(campaignStatusOf);
  if (statuses.includes("已暂停")) return "已暂停";
  if (statuses.includes("进行中")) return "进行中";
  if (statuses.includes("收尾中")) return "收尾中";
  if (statuses.includes("规划中")) return "规划中";
  if (statuses.includes("已结束")) {
    return statuses.every((status) => status === "已结束" || status === "已取消") ? "已结束" : "进行中";
  }
  return "已取消";
}

function rowKolCount(row: VkpiProjectRow) {
  return typeof row.kolCount === "number" && row.kolCount > 0 ? row.kolCount : 1;
}

// 组内全 null(无归因/账本链路)→ null 显"—";只要有一行有真值就求和(旧 sumNullable 同口径)。
function sumNullable(rows: VkpiProjectRow[], pick: (row: VkpiProjectRow) => number | null | undefined): number | null {
  let hasValue = false;
  let total = 0;
  rows.forEach((row) => {
    const value = pick(row);
    if (value == null) return;
    hasValue = true;
    total += value;
  });
  return hasValue ? total : null;
}

export interface CampaignGroupLite {
  id: string;
  title: string;
  status: CampaignStatus;
  kolCount: number;
  cost: number | null;
  gmv: number | null;
}

export function buildCampaignGroupsLite(projects: VkpiProjectRow[]): CampaignGroupLite[] {
  const grouped = new Map<string, VkpiProjectRow[]>();
  projects.forEach((project) => {
    const key = String(project.campaign || "").trim().toLowerCase() || `project:${project.id}`;
    grouped.set(key, [...(grouped.get(key) || []), project]);
  });
  return Array.from(grouped.entries()).map(([id, rows]) => ({
    id,
    title: rows[0]?.campaign || "未命名项目",
    status: groupStatusOf(rows),
    kolCount: rows.reduce((sum, row) => sum + rowKolCount(row), 0),
    cost: sumNullable(rows, (row) => row.cost),
    gmv: sumNullable(rows, (row) => row.gmv),
  }));
}

/** 在项 KOL 口径:规划中/进行中/收尾中 组的 KOL 数合计(暂停/终态/取消不算在项)。 */
const IN_FLIGHT = new Set<CampaignStatus>(["规划中", "进行中", "收尾中"]);
export function countInFlightKols(groups: CampaignGroupLite[]): number {
  return groups.filter((group) => IN_FLIGHT.has(group.status)).reduce((sum, group) => sum + group.kolCount, 0);
}

/* ============ KPI 带四卡(LaunchKpiBand 同构;趋势线 = board-series 真按日序列,
   单源失败 boardSeriesVals=null → spempty 诚实虚线;环比药丸只挂「本窗发布」
   (卡面大数与序列同口径 30 天窗),关联指标卡绝不冒充大数环比) ============ */

export function ProjectsKpiBand({
  activeProjects,
  activeNote,
  inFlightKols,
  kolsNote,
  posted30d,
  postedNote,
  revenueUsd,
  revenueNote,
  boardSeries,
}: {
  activeProjects: number | null;
  activeNote: string;
  inFlightKols: number | null;
  kolsNote: string;
  posted30d: number | null;
  postedNote: string;
  revenueUsd: number | null;
  revenueNote: string;
  /** board-series?board=projects 响应(null=未就绪/失败 → 四卡 spempty 诚实虚线) */
  boardSeries?: VkpiBoardSeriesResponse | null;
}) {
  const bs = boardSeries ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {activeProjects != null ? (
        <KpiCard
          label="进行中项目"
          value={activeProjects.toLocaleString()}
          unit="个"
          series={boardSeriesVals(bs, "projects_new")}
          seriesColor="var(--ds-accent)"
        />
      ) : (
        <KpiCard label="进行中项目" value="—" pending pendingNote={activeNote} />
      )}
      {inFlightKols != null ? (
        <KpiCard
          label="在项 KOL"
          value={inFlightKols.toLocaleString()}
          unit="人"
          series={boardSeriesVals(bs, "stage_advances")}
          seriesColor="var(--ds-accent-2)"
        />
      ) : (
        <KpiCard label="在项 KOL" value="—" pending pendingNote={kolsNote} />
      )}
      {posted30d != null ? (
        <KpiCard
          label="本窗发布"
          value={posted30d.toLocaleString()}
          unit="条 · 30天"
          series={boardSeriesVals(bs, "content_posted")}
          seriesColor="var(--ds-good)"
          delta={boardSeriesDelta(bs, "content_posted")}
        />
      ) : (
        <KpiCard label="本窗发布" value="—" pending pendingNote={postedNote} />
      )}
      {revenueUsd != null ? (
        <KpiCard
          label="归因销售"
          value={fmtUsd2(revenueUsd)}
          series={boardSeriesVals(bs, "attribution_revenue_cents")}
          seriesColor="var(--ds-info)"
        />
      ) : (
        <KpiCard label="归因销售" value="—" pending pendingNote={revenueNote} />
      )}
    </div>
  );
}

/* ============ 观察窗口行(签收→开窗环节;点行 → 详情连续翻) ============ */

export function windowStatusPill(status: string) {
  const meta = WINDOW_STATUS[String(status)] || { label: String(status || "—"), cls: "border-line text-muted" };
  return <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold tracking-[0.05em] ${meta.cls}`}>{meta.label}</span>;
}

function shortDay(value: unknown): string {
  const s = String(value || "");
  return s.length >= 10 ? s.slice(0, 10) : s || "—";
}

export function WindowRowLine({ item, index, onOpen }: { item: Row; index: number; onOpen: (i: number) => void }) {
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      {windowStatusPill(String(item.status || ""))}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {String(item.project_name || `项目 #${item.project_id ?? "—"}`)}
        {item.product_name ? <span className="ml-1.5 text-[9.5px] text-muted">{String(item.product_name)}</span> : null}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title="观察窗口起止(UTC 存)">
        {shortDay(item.starts_at)} → {shortDay(item.ends_at)}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title={item.last_scan_at ? `最近扫描 ${String(item.last_scan_at)}(UTC)` : "尚未扫描"}>
        扫 ×{Number(item.scan_count) || 0}
      </span>
      {item.matched_content_post_id != null ? (
        <span className="flex-none rounded-[5px] border border-good bg-good-soft px-1 py-px text-[8px] font-bold text-good" title={`已回填内容帖 #${item.matched_content_post_id}`}>
          帖 #{String(item.matched_content_post_id)}
        </span>
      ) : null}
    </div>
  );
}

export function WindowsBody({
  items,
  total,
  onOpen,
  onOpenAll,
}: {
  items: Row[];
  total: number;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
}) {
  if (items.length === 0) return <EmptyLine text="0 个窗口(无 delivered 签收或未开窗,如实)。" />;
  return (
    <div>
      {items.slice(0, FACE_ROWS).map((item, i) => (
        <WindowRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} />
      ))}
      {total > FACE_ROWS && (
        <button
          type="button"
          onClick={onOpenAll}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量 {total} 个 · 点单条连续翻
        </button>
      )}
    </div>
  );
}

/* ============ 归因销售行(美分→美元;置信徽;绝对时间) ============ */

export function confidencePill(confidence: string) {
  const meta = ATTRIBUTION_CONFIDENCE[String(confidence)] || { label: String(confidence || "—"), cls: "border-line text-muted" };
  return <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold tracking-[0.05em] ${meta.cls}`}>{meta.label}</span>;
}

export function AttributionRowLine({ item, index, onOpen }: { item: Row; index: number; onOpen: (i: number) => void }) {
  const usd = centsToUsd(item.revenue_cents);
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(String(item.source_platform || ""))}
      </span>
      {confidencePill(String(item.confidence || ""))}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {String(item.order_id || item.source_ref || `归因 #${item.id}`)}
        {item.product_sku ? <span className="ml-1.5 text-[9.5px] text-muted">{String(item.product_sku)}</span> : null}
      </span>
      <span className="flex-none font-mono text-[10.5px] text-ink" title="订单收入(revenue_cents ÷ 100)">
        {fmtUsd2(usd)}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title={item.occurred_at ? `${String(item.occurred_at)}(UTC 存 · 按浏览器时区显示)` : "无时间戳"}>
        {formatLocal(String(item.occurred_at || "") || null)}
      </span>
    </div>
  );
}

export function AttributionBody({
  items,
  total,
  onOpen,
  onOpenAll,
}: {
  items: Row[];
  total: number;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
}) {
  if (items.length === 0) return <EmptyLine text="0 行 · 归因链路已建但窗口内无订单(如实)。" />;
  return (
    <div>
      {items.slice(0, FACE_ROWS).map((item, i) => (
        <AttributionRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} />
      ))}
      {total > FACE_ROWS && (
        <button
          type="button"
          onClick={onOpenAll}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量 {total} 行 · 点单条连续翻
        </button>
      )}
    </div>
  );
}

/* ============ 成本概览(项目组 Σ cost 条形 Top;null=无账本链路如实跳过) ============ */

export function CostsBody({ groups }: { groups: CampaignGroupLite[] }) {
  const priced = groups.filter((group) => group.cost != null && (group.cost as number) > 0);
  if (priced.length === 0) return <EmptyLine text="项目组均无成本账本行(vkpi_cost_ledger 未挂本组项目,如实)。" />;
  const top = [...priced].sort((a, b) => (b.cost as number) - (a.cost as number)).slice(0, 8);
  const max = Math.max(...top.map((group) => group.cost as number));
  const totalUsd = priced.reduce((sum, group) => sum + (group.cost as number), 0);
  return (
    <div>
      {top.map((group) => (
        <BarRow
          key={group.id}
          name={group.title}
          widthPct={max > 0 ? ((group.cost as number) / max) * 100 : 0}
          value={fmtUsd2(group.cost)}
          title={`${group.title} · ${group.status}`}
        />
      ))}
      <div className="vkpi-prov-note">
        有账本 {priced.length}/{groups.length} 组 · 合计 {fmtUsd2(totalUsd)} · null=无账本链路不计入
      </div>
    </div>
  );
}
