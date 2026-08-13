import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { CoverRow, EmptyLine, type Row } from "./MarketVoicePage.modules";
import { BarRow } from "./MarketVoicePage.charts";
import { platformBadge } from "./MarketVoicePage.dialogs";
import {
  APPROVAL_STATUS,
  CONTENT_POST_STATUS,
  LAUNCH_STATUS,
  STAGE_LABELS,
} from "../../../../services/vkpi/launchBoard-api";
import type { useContentReview, usePublishActions } from "./LaunchPadBoardPage.actions";
import { kolHumanDisplayName } from "../lib/kolIdentity";

// 发射台 · 运营侧模块 body(内容排期 / 发布审批 / 发布计划 / 履约阶段 / 物料覆盖)。
//   行语言 = 金样板 FeedRowLine 同构:平台徽 + 标题 + 状态徽 + mono 绝对时间 + ↗ 原帖;
//   高频动作一键直达:候选行内 ✓ 确认 / ✕ 剔除(PATCH 真端点),待审批行内 ✓ 通过
//   (POST /publish/approve),状态只在端点真实返回后落地(actions hooks 纪律)。
//   卡面收敛 6 条,全量走弹窗(page 层 dialogs)。
// 红线:本文件零直连网络(动作走 hooks 回调);不触 viltrox_fit_score / rule_v0;
//   颜色全 token 零写死色;禁 opacity 修饰类;时间一律绝对时间戳(存 UTC 按浏览器时区)。

export const FACE_ROWS = 6; // demo FULL.slice(0,6):卡面收敛条数,全量走弹窗

type ReviewHook = ReturnType<typeof useContentReview>;
type PublishHook = ReturnType<typeof usePublishActions>;

export function statusPill(map: Record<string, { label: string; cls: string }>, status: string) {
  const meta = map[String(status)] || { label: String(status || "—"), cls: "border-line text-muted" };
  return <span className={`flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold tracking-[0.05em] ${meta.cls}`}>{meta.label}</span>;
}

const MINI_ACT =
  "flex-none rounded-[5px] border border-line px-1.5 py-px text-[9px] text-muted transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";

const keyActivate = (fn: () => void) => (ev: React.KeyboardEvent) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    fn();
  }
};

/* ============ 内容排期行(候选行内 ✓/✕ 一键复核;点行 → 详情连续翻) ============ */
export function PostRowLine({
  item,
  index,
  onOpen,
  review,
}: {
  item: Row;
  index: number;
  onOpen: (i: number) => void;
  review: ReviewHook;
}) {
  const id = Number(item.id) || 0;
  const status = String(review.reviewed[id] ?? item.status ?? "");
  const busy = review.busyId === id;
  const canReview = status === "candidate" || status === "needs_review";
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(String(item.platform || ""))}
      </span>
      {statusPill(CONTENT_POST_STATUS, status)}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent" title={String(item.project_name || "")}>
        {String(item.title || item.content_url || "—")}
      </span>
      {canReview && (
        <>
          <button
            type="button"
            className={MINI_ACT}
            disabled={busy}
            title="标记已确认(回填观察窗口)"
            onClick={(ev) => {
              ev.stopPropagation();
              review.review(id, "matched");
            }}
          >
            {busy ? "…" : "✓ 确认"}
          </button>
          <button
            type="button"
            className={MINI_ACT}
            disabled={busy}
            title="标记剔除"
            onClick={(ev) => {
              ev.stopPropagation();
              review.review(id, "rejected");
            }}
          >
            ✕ 剔除
          </button>
        </>
      )}
      <span
        className="flex-none font-mono text-[9.5px] text-muted"
        title={item.published_at ? `${item.published_at}(UTC 存 · 按浏览器时区显示)` : "无发布时间"}
      >
        {formatLocal(String(item.published_at || item.created_at || ""))}
      </span>
      {item.content_url ? (
        <a
          className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none"
          href={String(item.content_url)}
          target="_blank"
          rel="noopener noreferrer"
          title={`${item.platform || "原帖"} · 直跳原帖`}
          onClick={(ev) => ev.stopPropagation()}
        >
          ↗
        </a>
      ) : null}
    </div>
  );
}

export function ContentSchedBody({
  items,
  total,
  review,
  onOpen,
  onOpenAll,
}: {
  items: Row[];
  total: number;
  review: ReviewHook;
  onOpen: (i: number) => void;
  onOpenAll: () => void;
}) {
  if (items.length === 0) return <EmptyLine text="0 条 · 无观察窗口扫到内容(物流断流如实)。" />;
  return (
    <div>
      {items.slice(0, FACE_ROWS).map((item, i) => (
        <PostRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} review={review} />
      ))}
      {review.error && (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">复核失败:{review.error}</div>
      )}
      {total > FACE_ROWS && (
        <button
          type="button"
          onClick={onOpenAll}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量 {total} 条 · 点单条连续翻
        </button>
      )}
    </div>
  );
}

/* ============ 发布审批行(pending 行内 ✓ 通过一键直达;点行 → 详情) ============ */
export function ApprovalRowLine({
  item,
  index,
  onOpen,
  publish,
}: {
  item: Row;
  index: number;
  onOpen: (i: number) => void;
  publish: PublishHook;
}) {
  const key = `${item.source_table}:${item.source_id}`;
  const state = publish.states[key] || {};
  const status = state.approved ? "approved" : state.scheduledAt ? "scheduled" : String(item.status || "pending");
  const busy = publish.busyKey === key;
  const accountName = item.account_handle
    ? kolHumanDisplayName({ display_name: item.account_name, handle: item.account_handle, platform: item.platform })
    : "";
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={keyActivate(() => onOpen(index))}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(String(item.platform || ""))}
      </span>
      {statusPill(APPROVAL_STATUS, status)}
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent" title={accountName}>
        {String(item.title || accountName || `${item.source_table} #${item.source_id}`)}
      </span>
      {status === "pending" && (
        <button
          type="button"
          className={MINI_ACT}
          disabled={busy}
          title="审批通过"
          onClick={(ev) => {
            ev.stopPropagation();
            publish.approve(String(item.source_table), String(item.source_id), {
              platform: String(item.platform || ""),
              account_handle: String(item.account_handle || ""),
              title: String(item.title || ""),
            });
          }}
        >
          {busy ? "…" : "✓ 通过"}
        </button>
      )}
      <span
        className="flex-none font-mono text-[9.5px] text-muted"
        title={item.scheduled_publish_at ? `计划发布 ${item.scheduled_publish_at}(UTC 存 · 按浏览器时区显示)` : "未排发布时间"}
      >
        {formatLocal(String(state.scheduledAt || item.scheduled_publish_at || item.created_at || ""))}
      </span>
    </div>
  );
}

export function ApprovalsBody({
  items,
  publish,
  onOpen,
}: {
  items: Row[];
  publish: PublishHook;
  onOpen: (i: number) => void;
}) {
  if (items.length === 0) {
    return <EmptyLine text="0 条 · 已建未用(入队方 = 内容排期行的审批 / 重排动作)。" />;
  }
  return (
    <div>
      {items.slice(0, FACE_ROWS).map((item, i) => (
        <ApprovalRowLine key={`${item.id}-${i}`} item={item} index={i} onOpen={onOpen} publish={publish} />
      ))}
      {publish.error && (
        <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[11px] text-crit">动作失败:{publish.error}</div>
      )}
    </div>
  );
}

/* ============ 发布计划行(名称 + SKU + 状态 + 窗口) ============ */
export function LaunchesBody({ launches }: { launches: Row[] }) {
  if (launches.length === 0) return <EmptyLine text="0 行 · 尚无发布计划。" />;
  return (
    <div>
      {launches.map((item) => (
        <div key={String(item.id)} className="flex min-w-0 items-center gap-2 border-b border-line py-2 text-[11.5px] last:border-0">
          {statusPill(LAUNCH_STATUS, String(item.status || ""))}
          <span className="min-w-0 flex-1 truncate text-ink-2" title={String(item.product_name || "")}>
            {String(item.name || item.product_name || "—")}
          </span>
          <span className="flex-none font-mono text-[9.5px] text-muted">{String(item.product_sku || "—")}</span>
          <span
            className="flex-none font-mono text-[9.5px] text-muted"
            title="发布窗口(UTC 存 · 按浏览器时区显示)"
          >
            {formatLocal(String(item.launch_window_start || ""))} → {formatLocal(String(item.launch_window_end || ""))}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ============ 履约阶段条形(归一阶段 × 指派行计数) ============ */
export function StagesBody({ stages, total }: { stages: Array<{ stage: string; n: number }>; total: number }) {
  if (stages.length === 0) return <EmptyLine text="0 行 · 无指派记录。" />;
  const max = Math.max(0, ...stages.map((s) => s.n));
  return (
    <div>
      {stages.map((s) => (
        <BarRow
          key={s.stage}
          name={STAGE_LABELS[s.stage] || s.stage}
          widthPct={max > 0 ? (s.n / max) * 100 : 0}
          value={`${s.n.toLocaleString()} · ${total > 0 ? ((s.n / total) * 100).toFixed(1) : "0"}%`}
        />
      ))}
    </div>
  );
}

/* ============ 物料覆盖(三表静态盘点;无读端点 = 盲区如实) ============ */
export function MaterialsBody() {
  return (
    <div>
      <CoverRow on={false} name="官方物料库" table="vkpi_legacy_official_materials_staging" value="241 行 · 无读端点" note="Excel 导入 staging · 读端点待接" />
      <CoverRow on={false} name="活动物料" table="vkpi_event_materials" value="0 行 · 已建未用" />
      <CoverRow on={false} name="内容资产" table="vkpi_content_assets" value="0 行 · 已建未用" />
      <div className="vkpi-prov-note">静态盘点 2026-07-12 · 非实时 · 接通读端点后自动点亮</div>
    </div>
  );
}
