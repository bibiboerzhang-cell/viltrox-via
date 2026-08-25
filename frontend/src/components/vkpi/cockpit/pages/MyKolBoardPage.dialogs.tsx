import React from "react";
import { SrcChip } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import { ModalShell, SectionLabel, platformBadge } from "./MarketVoicePage.dialogs";
import {
  refreshMyKolVideoMetrics,
  summarizeKolVideos,
  trackMyKolExistingVideo,
  type KolLibraryRow,
  type LibraryFilter,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import { KolLensUsageList } from "./MyKolBoardPage.charts";
import { getLensInsightsForKol, type KolLensUsage } from "../../../../services/vkpi/lensInsights-api";
import { ReceiptLine } from "./MyKolBoardPage.receipt";
import { TrackExistingVideoForm } from "./MyKolBoardPage.track-form";
import { DataWatchContext, runDataWatchAction, skuRequiredHintText, toggleIdInSet, type DataWatchSubmitIntent } from "./MyKolBoardPage.data-watch";
import { KolContentMonitoringSection } from "./MyKolBoardPage.content-monitoring";
import { runKeyframeQaAction } from "./MyKolBoardPage.keyframe-qa";
import { RecoveryPollingLine } from "./MyKolBoardPage.video-tasks";
import { useMyKolVideoRecovery } from "./useMyKolVideoRecovery";
import {
  CHIP,
  CHIP_OFF,
  CHIP_ON,
  CoopResultsSection,
  evidenceStatText,
  GoaffproTrackSection,
  KolVideoSection,
  MINI_BADGE,
  useKolEvidenceStats,
  type KolEvidenceStats,
} from "./MyKolBoardPage.libdetail";
import {
  commentsTerminalReceipt,
  missingJobIdReceipt,
  waitJobTerminal,
  type FlowReceipt,
} from "../../pages/myKol/PoolEvidenceContent.helpers";
import type { VkpiProjectRow } from "../../vkpiTypes";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { kolHumanDisplayName, kolHumanPublicHandle } from "../lib/kolIdentity";

// MY KOL 弹窗：网络统一经 services/http；排队、运行、完成三态严格分层。
// 纯展示不写 fit/rule_v0；播放合计仅计实测值，缺失值不当 0。

const NAV_BTN =
  "inline-flex min-h-9 items-center rounded-lg border border-line px-3 py-1.5 text-[11.5px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:text-muted";
const ACT_BTN =
  "inline-flex min-h-9 items-center justify-center rounded-lg border border-line px-3 py-1.5 text-[11.5px] font-medium text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";
const MORE_BTN =
  "mt-2.5 min-h-10 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[11.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft";
const FIELD = "min-h-9 rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-ink-2 outline-none focus:border-accent";

export { ReceiptLine } from "./MyKolBoardPage.receipt";

const errText = (err: unknown, fallback: string) =>
  String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || fallback).slice(0, 100);

function videoWriteError(err: unknown, action: string): string {
  const code = errText(err, "请重试");
  if (code === "new_video_target_resolution_required") {
    return "当前仅支持已采集视频，请先账号补采/深爬后重试";
  }
  const status = Number((err as { status?: unknown })?.status || 0);
  if (status === 401 || status === 403 || /not_writable|not_owned|forbidden|permission/i.test(code)) {
    return `${action}失败：服务端拒绝写入，当前账号对该 KOL 可能只有共享只读权限。`;
  }
  return `${action}失败：${code}`;
}

function parseProductSkus(value: string): string[] {
  return [...new Set(value.split(/[,，\n]/).map((part) => part.trim()).filter(Boolean))];
}

const humanLibraryName = (row: KolLibraryRow | null | undefined) => (
  kolHumanDisplayName(row as unknown as Record<string, unknown> | undefined)
);
const humanLibraryHandle = (row: KolLibraryRow | null | undefined) => (
  kolHumanPublicHandle(row as unknown as Record<string, unknown> | undefined)
);

function KolAvatar({ row, size = "row" }: { row: KolLibraryRow; size?: "row" | "detail" }) {
  const humanName = humanLibraryName(row);
  const resolvedSrc = proxiedImageUrl(row.avatarUrl);
  const [failed, setFailed] = React.useState(!resolvedSrc);
  React.useEffect(() => setFailed(!resolvedSrc), [resolvedSrc]);
  const avatarClass = size === "detail"
    ? "h-9 w-9 flex-none rounded-full border border-line object-cover"
    : "h-[22px] w-[22px] flex-none rounded-full border border-line object-cover";
  const fallbackClass = size === "detail"
    ? "grid h-9 w-9 flex-none place-items-center rounded-full border border-line bg-card text-[14px] text-muted"
    : "grid h-[22px] w-[22px] flex-none place-items-center rounded-full border border-line bg-panel text-[10px] text-muted";

  if (failed || !resolvedSrc) {
    return (
      <span
        aria-label={`${humanName} 头像暂不可用`}
        className={fallbackClass}
      >
        {humanName.slice(0, 1).toUpperCase()}
      </span>
    );
  }

  return (
    <img
      src={resolvedSrc}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      className={avatarClass}
      onError={() => setFailed(true)}
      onLoad={(event) => {
        const image = event.currentTarget;
        if (image.naturalWidth <= 2 && image.naturalHeight <= 2) setFailed(true);
      }}
    />
  );
}

/* ============ 库行(卡面 6 条与全量弹窗共用;点行开详情,弹窗内可勾选批量) ============ */
export function KolRowLine({
  row,
  index,
  onOpen,
  selectable = false,
  selected = false,
  onToggleSelect,
  stats,
}: {
  row: KolLibraryRow;
  index: number;
  onOpen: (i: number) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (poolId: number) => void;
  /** 采集数据列(与详情弹窗同源同口径);undefined=读取中显 … */
  stats?: KolEvidenceStats;
}) {
  const humanName = humanLibraryName(row);
  const publicHandle = humanLibraryHandle(row);
  const onKey = (ev: React.KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      onOpen(index);
    }
  };
  return (
    <div className="group flex min-h-10 min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0" role="button" tabIndex={0} onClick={() => onOpen(index)} onKeyDown={onKey}>
      {selectable ? (
        <input type="checkbox" aria-label={`勾选 ${humanName}`} checked={selected} onChange={() => onToggleSelect?.(row.poolId)} onClick={(ev) => ev.stopPropagation()} className="h-3.5 w-3.5 flex-none accent-[var(--ds-accent)]" />
      ) : null}
      <KolAvatar row={row} />
      <span className="min-w-[42px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">{platformBadge(row.platform)}</span>
      <span className="min-w-0 flex-1 truncate text-[12.5px] leading-5 text-ink-2 transition-colors group-hover:text-accent">
        {humanName}
        {publicHandle ? <span className="ml-1.5 text-[10px] text-muted">{publicHandle}</span> : null}
      </span>
      {row.isShared ? (
        <span className={`${MINI_BADGE} border-accent-2 text-accent-2`} title={row.sharedByName ? `来自 ${row.sharedByName} 的共享` : "共享给我(只读可见)"}>共享</span>
      ) : (
        <span className={`${MINI_BADGE} border-line text-muted`} title="我的收藏">收藏</span>
      )}
      {row.claim ? (
        <span className={`${MINI_BADGE} border-good bg-good-soft text-good`} title={row.claim.expiresAt ? `本人认领 · 至 ${formatLocal(row.claim.expiresAt)}` : "本人认领"}>已认领</span>
      ) : null}
      {row.projects.length > 0 ? (
        <span className={`${MINI_BADGE} border-accent bg-accent-soft text-accent`} title={`合作推进中 · ${row.projects.length} 个项目`}>进行中</span>
      ) : null}
      <span className="w-[64px] flex-none text-right font-mono text-[9.5px] text-muted" title="粉丝数">{row.followers != null ? row.followers.toLocaleString() : "—"}</span>
      <span className="w-[46px] flex-none text-right font-mono text-[9.5px] text-muted" title="Fit 分(只读展示)">{row.fit != null ? `Fit ${Math.round(row.fit)}` : "—"}</span>
      <span
        className="hidden w-[150px] flex-none truncate text-right font-mono text-[9px] text-muted sm:inline"
        title={stats ? "已采集视频 · 实测播放合计 · 已深析(与详情弹窗同源同口径;未实测播放已剔除,— = 尚无采集)" : "采集数据读取中…"}
      >
        {evidenceStatText(stats)}
      </span>
    </div>
  );
}

/* ============ 筛选 chips(卡面与全量弹窗同款):有V视频/全部 + 平台 strip + 负责人 ============ */
export function LibraryChips({
  filter,
  onFilter,
  platformOptions,
  vKolCount,
  staff,
  mine,
}: {
  filter: LibraryFilter;
  onFilter: (next: LibraryFilter) => void;
  platformOptions: Array<{ platform: string; count: number }>;
  /** board-ext 全库有 V 相关视频的 KOL 总数;null = 聚合未就绪(chip 不带数,不编) */
  vKolCount: number | null;
  /** 负责人筛选(管理层;走服务端 scope 重取,零本地猜);缺省不渲染 */
  staff?: { options: Array<{ id: string; name: string }>; value: string; onChange: (id: string) => void; busy?: boolean };
  /** 「我的收藏」快捷筛选(viewer 本人收藏名单;名单未就绪不渲染,绝不摆假 chip) */
  mine?: { on: boolean; count: number; onToggle: () => void };
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={`${CHIP} ${filter.vOnly ? CHIP_ON : CHIP_OFF}`} onClick={() => onFilter({ ...filter, vOnly: true })} title="全库有确定 V 信号(合作/标题提及)的 KOL 总数;点选按名单精确过滤,单条三档判定见详情">
          有 V 视频{vKolCount != null ? ` (${vKolCount.toLocaleString()})` : ""}
        </button>
        <button type="button" className={`${CHIP} ${filter.vOnly ? CHIP_OFF : CHIP_ON}`} onClick={() => onFilter({ ...filter, vOnly: false })}>全部</button>
        {mine ? (
          <button type="button" className={`${CHIP} ${mine.on ? CHIP_ON : CHIP_OFF}`} onClick={mine.onToggle} title="只看我自己收藏的 KOL(共享给我的与他人收藏不计;管理层全团队视图一键切回自己的)">
            我的收藏 ({mine.count.toLocaleString()})
          </button>
        ) : null}
        <span className="mx-0.5 h-[14px] w-px flex-none bg-line" />
        <button type="button" className={`${CHIP} ${filter.platform ? CHIP_OFF : CHIP_ON}`} onClick={() => onFilter({ ...filter, platform: "" })}>全部平台</button>
        {platformOptions.map((opt) => (
          <button key={opt.platform} type="button" className={`${CHIP} ${filter.platform === opt.platform ? CHIP_ON : CHIP_OFF}`} onClick={() => onFilter({ ...filter, platform: filter.platform === opt.platform ? "" : opt.platform })}>
            {platformBadge(opt.platform)} {opt.count}
          </button>
        ))}
        {staff ? (
          <select aria-label="负责人筛选" value={staff.value} disabled={staff.busy} onChange={(ev) => staff.onChange(ev.target.value)} className={`ml-auto ${FIELD}`}>
            <option value="">负责人 · 全员</option>
            {staff.options.map((member) => (
              <option key={member.id} value={member.id}>{member.name}</option>
            ))}
          </select>
        ) : null}
      </div>
      {filter.vOnly ? (
        <div className="mt-1.5 text-[11px] leading-4 text-muted">
          按全库 V 信号名单精确过滤(合作 / 标题提及 · 派生判据非采集字段){vKolCount != null ? `;名单 ${vKolCount.toLocaleString()} 人` : ""} · 单条视频三档判定见详情
        </div>
      ) : null}
    </div>
  );
}

/* ============ 全量弹窗(FeedListModal 同构):搜索 + 同款 chips + 批量工具条 + 可滚列表 ============ */
const LIST_PAGE = 60;

export function KolLibraryListModal({
  apiToken,
  rows,
  totalAll,
  filter,
  onFilter,
  platformOptions,
  vKolCount,
  staff,
  mine,
  projects,
  onOpenDetail,
  onClose,
  onActionDone,
}: {
  apiToken: string;
  /** 已过滤后的全量行(客户端过滤;数据源 aggregate 一次全量下发) */
  rows: KolLibraryRow[];
  totalAll: number;
  filter: LibraryFilter;
  onFilter: (next: LibraryFilter) => void;
  platformOptions: Array<{ platform: string; count: number }>;
  vKolCount: number | null;
  staff?: React.ComponentProps<typeof LibraryChips>["staff"];
  mine?: React.ComponentProps<typeof LibraryChips>["mine"];
  projects: VkpiProjectRow[];
  onOpenDetail: (i: number) => void;
  onClose: () => void;
  onActionDone?: () => void;
}) {
  const [visible, setVisible] = React.useState(LIST_PAGE);
  const [selected, setSelected] = React.useState<Set<number>>(new Set());
  const [projId, setProjId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState<FlowReceipt | null>(null);
  React.useEffect(() => setVisible(LIST_PAGE), [filter.vOnly, filter.platform, filter.query]);

  const shown = rows.slice(0, visible);
  // 采集数据列:只拉当前页可见行(模块级缓存,与卡面/详情同源);翻页增量取。
  const shownIds = React.useMemo(() => shown.map((row) => row.poolId), [shown]);
  const statsMap = useKolEvidenceStats(apiToken, shownIds);
  const selectedRows = React.useMemo(() => rows.filter((row) => selected.has(row.poolId)), [rows, selected]);
  const allVisibleSelected = shown.length > 0 && shown.every((row) => selected.has(row.poolId));
  const toggleSelect = (poolId: number) =>
    setSelected((prev) => { const next = new Set(prev); if (next.has(poolId)) next.delete(poolId); else next.add(poolId); return next; });
  const toggleSelectVisible = () =>
    setSelected((prev) => { const next = new Set(prev); shown.forEach((row) => (allVisibleSelected ? next.delete(row.poolId) : next.add(row.poolId))); return next; });

  // 【批量入项目】旧 MyKolPage.Sections 同款平移:POST /projects/{id}/kols 逐个调用
  // (后端幂等),逐条进度 + 部分失败如实统计;完成后触发父级聚合刷新。
  const runBatchAddToProject = async () => {
    if (!apiToken || busy || !selectedRows.length) return;
    if (!projId) return setNote({ text: "请先在下拉里选择目标项目", tone: "error" });
    setBusy(true);
    let inserted = 0, existing = 0, fail = 0, firstError = "";
    try {
      const { addKolsToProject } = await import("../../../../services/vkpi/projects-api");
      for (let i = 0; i < selectedRows.length; i += 1) {
        setNote({ text: `批量入项目中… ${i + 1}/${selectedRows.length}`, tone: "info" });
        try {
          const result = await addKolsToProject(apiToken, projId, [String(selectedRows[i].poolId)]);
          const added = Number(result.inserted || 0);
          const skipped = Number(result.skipped_existing || 0);
          const missing = Array.isArray(result.missing_kol_pool_ids) ? result.missing_kol_pool_ids.length : 0;
          if (added > 0) inserted += added;
          else if (skipped > 0 && missing === 0) existing += skipped;
          else {
            fail += 1;
            if (!firstError) firstError = missing ? "KOL 已不存在" : "服务端未确认写入";
          }
        } catch (err) {
          fail += 1;
          if (!firstError) firstError = errText(err, "").slice(0, 60);
        }
      }
    } catch (err) {
      setBusy(false);
      return setNote({ text: `批量入项目失败:${errText(err, "请重试")}`, tone: "error" });
    }
    setBusy(false);
    setNote({
      text: `批量入项目完成:新增 ${inserted} 个${existing ? ` · 已存在 ${existing} 个` : ""}${fail ? ` · 未写入 ${fail} 个${firstError ? `(首个原因:${firstError})` : ""}` : ""}`,
      tone: fail ? "error" : inserted ? "ok" : "info",
    });
    if (inserted || existing) window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
    if (inserted) {
      window.dispatchEvent(new CustomEvent("vkpi:projects-changed"));
      onActionDone?.();
    }
  };

  // 【导出 CSV】纯前端拼装下载(myKolBatch 现成 helper),邮箱按读端口径脱敏。
  const runExportCsv = async () => {
    if (!selectedRows.length || busy) return;
    try {
      const { buildKolCsv, downloadCsv } = await import("../../pages/myKol/myKolBatch");
      const csv = buildKolCsv(
        selectedRows.map((row) => ({
          name: row.name,
          platform: row.platform,
          handle: row.handle,
          followers: row.followers != null ? row.followers.toLocaleString() : "",
          fit: row.fit,
          email: row.email,
        })),
      );
      downloadCsv(`my-kol-selected-${new Date().toISOString().slice(0, 10)}.csv`, csv);
      setNote({ text: `已导出 ${selectedRows.length} 行 CSV(邮箱已脱敏)`, tone: "ok" });
    } catch (err) {
      setNote({ text: `导出失败:${errText(err, "请重试")}`, tone: "error" });
    }
  };

  // 【批量受众画像】audience-stats/refresh 逐个入队(status:error 如实计失败,不冒充成功)。
  const runBatchAudience = async () => {
    if (!apiToken || busy || !selectedRows.length) return;
    const writableRows = selectedRows.filter((row) => !row.isShared);
    const sharedSkipped = selectedRows.length - writableRows.length;
    if (!writableRows.length) {
      setNote({ text: "所选 KOL 均为共享只读，不能批量发起受众画像；请由收藏负责人执行。", tone: "info" });
      return;
    }
    if (writableRows.length > 10 && !window.confirm(`选中 ${writableRows.length} 个可写 KOL,受众画像逐个刷新可能耗时较久,确认继续?`)) return;
    setBusy(true);
    let ok = 0, fail = 0, firstReason = "";
    try {
      const { refreshAudienceStats } = await import("../../../../services/vkpi/kolPool-api");
      for (let i = 0; i < writableRows.length; i += 1) {
        setNote({ text: `受众画像入队中… ${i + 1}/${writableRows.length}`, tone: "info" });
        try {
          const resp = await refreshAudienceStats(apiToken, writableRows[i].poolId);
          if (String(resp?.status || "") === "error") {
            fail += 1;
            if (!firstReason) firstReason = String(resp?.reason || "").slice(0, 60);
          } else ok += 1;
        } catch (err) {
          fail += 1;
          if (!firstReason) firstReason = errText(err, "").slice(0, 60);
        }
      }
    } catch (err) {
      setBusy(false);
      return setNote({ text: `受众画像入队失败:${errText(err, "请重试")}`, tone: "error" });
    }
    setBusy(false);
    setNote({ text: `受众画像已受理 ${ok} 个${fail ? ` · 失败 ${fail} 个${firstReason ? `(首个原因:${firstReason})` : ""}` : ""}${sharedSkipped ? ` · 跳过共享只读 ${sharedSkipped} 个` : ""}——结果以后端计算为准`, tone: fail ? "error" : "ok" });
  };

  const projectOptions = projects.filter((project) => Number.isFinite(Number(project.id)));
  return (
    <ModalShell title="KOL 库 · 全量" sub={`筛出 ${rows.length} 条 / 在库 ${totalAll} 条 · 点单条看详情(详情内 ↑↓ 连续翻)`} onClose={onClose} maxWidth="max-w-[760px]">
      <input type="search" value={filter.query} onChange={(ev) => onFilter({ ...filter, query: ev.target.value })} placeholder="搜索名称 / handle…" aria-label="搜索 KOL" className="mb-2.5 w-full rounded-[10px] border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent" />
      <div className="mb-2.5">
        <LibraryChips filter={filter} onFilter={onFilter} platformOptions={platformOptions} vKolCount={vKolCount} staff={staff} mine={mine} />
      </div>
      <div aria-label="批量工具条" className="mb-2.5 flex flex-wrap items-center gap-2 rounded-[10px] border border-line bg-panel px-2.5 py-2">
        <span className="text-[10.5px] text-muted">已选 {selectedRows.length}</span>
        <button type="button" className={ACT_BTN} onClick={toggleSelectVisible}>{allVisibleSelected ? "取消全选可见" : "全选可见"}</button>
        <button type="button" className={ACT_BTN} disabled={!selected.size} onClick={() => setSelected(new Set())}>清空</button>
        <select aria-label="批量入项目目标" value={projId} onChange={(ev) => setProjId(ev.target.value)} className={FIELD}>
          <option value="">选择项目…</option>
          {projectOptions.map((project) => (
            <option key={project.id} value={String(project.id)}>{project.campaign || `项目 ${project.id}`}</option>
          ))}
        </select>
        <button type="button" className={ACT_BTN} disabled={busy || !selectedRows.length} onClick={runBatchAddToProject}>批量入项目</button>
        <button type="button" className={ACT_BTN} disabled={busy || !selectedRows.length} onClick={runExportCsv}>导出 CSV</button>
        <button type="button" className={ACT_BTN} disabled={busy || !selectedRows.some((row) => !row.isShared)} title={selectedRows.some((row) => !row.isShared) ? "仅对本人收藏的可写 KOL 入队；共享行自动跳过" : "所选行均为共享只读"} onClick={runBatchAudience}>批量受众画像</button>
      </div>
      <ReceiptLine msg={note} />
      <SectionLabel>库内 KOL</SectionLabel>
      {rows.length === 0 ? (
        <div className="px-3 py-4 text-center text-[12px] text-muted">该筛选组合下 0 条——诚实空,不编行。</div>
      ) : (
        shown.map((row, i) => <KolRowLine key={row.poolId} row={row} index={i} onOpen={onOpenDetail} selectable selected={selected.has(row.poolId)} onToggleSelect={toggleSelect} stats={statsMap.get(row.poolId)} />)
      )}
      {rows.length > visible ? (
        <button type="button" onClick={() => setVisible((v) => v + LIST_PAGE)} className={MORE_BTN}>
          ≡ 显示更多(已显示 {Math.min(visible, rows.length)}/{rows.length},数据已全量在本地)
        </button>
      ) : null}
    </ModalShell>
  );
}

/* ============ 详情弹窗(FeedDetailModal 同构):‹#n/N›+↑↓ 连续翻 + 档案卡 + 视频区 + 闭环动作排 ============ */

export function KolDetailModal({
  apiToken,
  rows,
  index,
  onNav,
  onClose,
  projects,
  onActionDone,
}: {
  apiToken: string;
  rows: KolLibraryRow[];
  index: number;
  onNav: (i: number) => void;
  onClose: () => void;
  projects: VkpiProjectRow[];
  onActionDone?: () => void;
}) {
  const item = rows[index];
  const humanName = humanLibraryName(item);
  const publicHandle = humanLibraryHandle(item);
  const total = rows.length;
  const poolId = Number(item?.poolId) || 0;
  const targetRef = React.useRef({ poolId: 0, epoch: 0 });
  if (targetRef.current.poolId !== poolId) {
    targetRef.current = { poolId, epoch: targetRef.current.epoch + 1 };
  }
  const mountedRef = React.useRef(true);
  React.useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const targetIsCurrent = (target: { poolId: number; epoch: number }) => (
    mountedRef.current && targetRef.current.poolId === target.poolId && targetRef.current.epoch === target.epoch
  );
  const cancelledRef = React.useRef(false);
  React.useEffect(() => {
    cancelledRef.current = false;
    return () => { cancelledRef.current = true; };
  }, [item?.poolId]);

  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") { ev.preventDefault(); onNav(index + 1); }
      else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") { ev.preventDefault(); onNav(index - 1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, onNav]);

  // 视频区以 keyset 分页、服务端持久任务态和轮询恢复为准。
  const recovery = useMyKolVideoRecovery({ apiToken, kolPoolId: item?.poolId });
  const videos: VkpiKolPoolVideoRow[] | null = recovery.loading && !recovery.loaded ? null : recovery.videos;
  const videosError = recovery.error;

  // viewer-context 读取失败时按钮保持不可用，不猜权限。
  const [viewer, setViewer] = React.useState<{ claimId: string; staffName: string; expiresAt: string; canRelease: boolean; canPaidActions: boolean; paidActionReason: string } | null>(null);
  const [viewerTick, setViewerTick] = React.useState(0);
  React.useEffect(() => {
    if (!apiToken || !item?.poolId) return;
    let alive = true;
    setViewer(null);
    import("../../../../services/vkpi/kol-api")
      .then(({ getMyKolViewerContext }) => getMyKolViewerContext(apiToken, item.poolId))
      .then((resp) => {
        if (!alive) return;
        const claim = resp?.claim;
        setViewer({ claimId: claim?.id != null ? String(claim.id) : "", staffName: String(claim?.staff_name || ""), expiresAt: String(claim?.expires_at || ""), canRelease: Boolean(claim?.can_release), canPaidActions: Boolean(resp?.paid_actions?.can_run_paid_actions), paidActionReason: String(resp?.paid_actions?.reason || "") });
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [apiToken, item?.poolId, viewerTick]);

  const [lensUsage, setLensUsage] = React.useState<KolLensUsage | null>(null);
  React.useEffect(() => {
    if (!apiToken || !item?.poolId) return;
    let alive = true;
    setLensUsage(null);
    getLensInsightsForKol(apiToken, item.poolId).then((resp) => { if (alive) setLensUsage(resp && typeof resp === "object" ? resp : null); }).catch(() => undefined);
    return () => { alive = false; };
  }, [apiToken, item?.poolId]);

  const [msgs, setMsgs] = React.useState<Record<string, FlowReceipt | null>>({});
  const [busyKeys, setBusyKeys] = React.useState<Set<string>>(new Set());
  const [queuedEvidence, setQueuedEvidence] = React.useState<Set<number>>(new Set());
  const [projId, setProjId] = React.useState("");
  const [trackUrl, setTrackUrl] = React.useState("");
  const [trackSkuInput, setTrackSkuInput] = React.useState("");
  const [trackBusy, setTrackBusy] = React.useState(false);
  const [refreshingEvidence, setRefreshingEvidence] = React.useState<Set<number>>(new Set());
  const [queuedRefreshEvidence, setQueuedRefreshEvidence] = React.useState<Set<number>>(new Set());
  const [trackingEvidence, setTrackingEvidence] = React.useState<Set<number>>(new Set());
  const [dataWatchEvidence, setDataWatchEvidence] = React.useState<Set<number>>(new Set());
  const skuInputRef = React.useRef<HTMLInputElement | null>(null);
  const dataWatchInteractionEpoch = React.useRef(0), pendingDetectedRef = React.useRef<{ video: VkpiKolPoolVideoRow; target: { poolId: number; epoch: number }; interactionEpoch: number } | null>(null);
  React.useEffect(() => {
    setMsgs({}); setQueuedEvidence(new Set()); setProjId("");
    setTrackUrl(""); setTrackSkuInput(""); setTrackBusy(false);
    setRefreshingEvidence(new Set()); setQueuedRefreshEvidence(new Set()); setTrackingEvidence(new Set()); setDataWatchEvidence(new Set()); pendingDetectedRef.current = null; dataWatchInteractionEpoch.current += 1;
  }, [item?.poolId]);
  const setMsg = (key: string, msg: FlowReceipt | null) => setMsgs((prev) => ({ ...prev, [key]: msg }));
  const setBusy = (key: string, on: boolean) =>
    setBusyKeys((prev) => { const next = new Set(prev); if (on) next.add(key); else next.delete(key); return next; });
  const loaded = videos || [];
  const collectedUrlVideos = React.useMemo(() => {
    const unique = new Map<string, VkpiKolPoolVideoRow>();
    (videos || []).forEach((video) => {
      const url = String(video.content_url || "").trim();
      if (url && !unique.has(url)) unique.set(url, video);
    });
    return [...unique.entries()].map(([url, video]) => ({ url, video }));
  }, [videos]);
  const { unanalyzed } = React.useMemo(() => summarizeKolVideos(loaded), [loaded]);
  const paidActionsReadOnly = Boolean(item.isShared || (viewer && !viewer.canPaidActions));
  const paidActionsReadOnlyHint = "共享 KOL 仅可查看；视频追踪、指标刷新、深析、评论采集和受众画像请由收藏负责人或管理层发起。";
  const runTrackVideo = async (direct?: { video: VkpiKolPoolVideoRow }) => {
    if (!apiToken || !poolId || paidActionsReadOnly) return;
    const cardEid = direct ? Number(direct.video.evidence_id ?? direct.video.id) || 0 : 0;
    if (direct ? trackingEvidence.has(cardEid) : trackBusy) return;
    const contentUrl = direct ? String(direct.video.content_url || "").trim() : trackUrl.trim();
    if (!contentUrl) { setMsg("tracking", { text: direct ? "该条未存原帖 URL,无法追踪;请先补采账号内容。" : "请先填写已采集视频 URL。", tone: "error" }); return; }
    const productSkus = direct ? [] : parseProductSkus(trackSkuInput);
    if (productSkus.length > 5) { setMsg("tracking", { text: "一次最多关联 5 个产品 SKU。", tone: "error" }); return; }
    const pendingDetected = direct ? null : pendingDetectedRef.current;
    if (pendingDetected) {
      const pendingUrl = String(pendingDetected.video.content_url || "").trim();
      if (!targetIsCurrent(pendingDetected.target) || dataWatchInteractionEpoch.current !== pendingDetected.interactionEpoch || contentUrl !== pendingUrl) { pendingDetectedRef.current = null; setMsg("tracking", { text: "检测确认上下文已失效，请重新点击该视频的『数据关注』。", tone: "error" }); return; }
      return runDataWatch(pendingDetected.video, productSkus, "confirm_detected");
    }
    const target = { ...targetRef.current }, interactionEpoch = ++dataWatchInteractionEpoch.current; pendingDetectedRef.current = null;
    if (direct) setTrackingEvidence((prev) => new Set(prev).add(cardEid)); else setTrackBusy(true);
    setMsg("tracking", null);
    try {
      const resp = await trackMyKolExistingVideo(apiToken, target.poolId, { contentUrl, productSkus });
      if (!targetIsCurrent(target) || dataWatchInteractionEpoch.current !== interactionEpoch) return;
      const status = String(resp?.status || "");
      if (status !== "queued" && status !== "already_queued") {
        setMsg("tracking", { text: `追踪请求未获服务端确认：${status || "未知状态"}`, tone: "error" });
        return;
      }
      const eid = Number(resp?.evidence_id) || cardEid || 0;
      if (eid > 0) setQueuedRefreshEvidence((prev) => new Set(prev).add(eid));
      setMsg("tracking", {
        text: `${status === "already_queued" ? "该视频的指标刷新已在队列中" : "指标刷新已排队"}${eid ? `（evidence #${eid}）` : ""}${productSkus.length ? ` · 已提交 ${productSkus.length} 个 SKU 关联` : ""}。`,
        tone: "info",
      });
      if (!direct) {
        setTrackUrl("");
        setTrackSkuInput("");
      }
      void recovery.refresh();
    } catch (err) {
      if (targetIsCurrent(target) && dataWatchInteractionEpoch.current === interactionEpoch) setMsg("tracking", { text: videoWriteError(err, "追踪"), tone: "error" });
    } finally {
      if (direct) setTrackingEvidence((prev) => { const next = new Set(prev); next.delete(cardEid); return next; });
      else setTrackBusy(false);
    }
  };
  const linkSkuFromCard = (video: VkpiKolPoolVideoRow, announce = true) => {
    const url = String(video.content_url || "").trim();
    if (!url) return;
    if (announce) { pendingDetectedRef.current = null; dataWatchInteractionEpoch.current += 1; setTrackSkuInput(""); }
    setTrackUrl(url);
    if (announce) setMsg("tracking", { text: `已选中「${String(video.title || video.video_title || `视频 #${Number(video.evidence_id ?? video.id) || 0}`)}」,填入 SKU 后点「追踪并排队刷新」。`, tone: "info" });
    window.requestAnimationFrame(() => {
      const input = skuInputRef.current;
      if (!input) return;
      input.focus();
      if (typeof input.scrollIntoView === "function") input.scrollIntoView({ block: "center" });
    });
  };
  function runDataWatch(video: VkpiKolPoolVideoRow, productSkus: string[] = [], submitIntent: DataWatchSubmitIntent = "auto") {
    const target = { ...targetRef.current };
    const interactionEpoch = ++dataWatchInteractionEpoch.current;
    if (submitIntent === "auto") { pendingDetectedRef.current = null; setTrackUrl(""); setTrackSkuInput(""); }
    else if (pendingDetectedRef.current) pendingDetectedRef.current = { ...pendingDetectedRef.current, target, interactionEpoch };
    return runDataWatchAction(video, {
      apiToken,
      kolPoolId: target.poolId,
      readOnly: paidActionsReadOnly,
      isCurrent: () => targetIsCurrent(target) && dataWatchInteractionEpoch.current === interactionEpoch,
      isBusy: (eid) => dataWatchEvidence.has(eid),
      setBusy: toggleIdInSet(setDataWatchEvidence),
      setReceipt: (msg) => setMsg("tracking", msg),
      onSkuRequired: (v, candidates) => { linkSkuFromCard(v); setMsg("tracking", { text: skuRequiredHintText(candidates), tone: "info" }); },
      onDetectedConfirmationRequired: (v, candidates) => { pendingDetectedRef.current = { video: v, target, interactionEpoch }; linkSkuFromCard(v, false); setTrackSkuInput(candidates.map((item) => String(item.sku_code || "").trim()).filter(Boolean).join(", ")); },
      onTracked: () => { if (submitIntent === "confirm_detected") { pendingDetectedRef.current = null; setTrackUrl(""); setTrackSkuInput(""); } void recovery.refresh(); },
    }, productSkus, submitIntent);
  }
  const runMetricRefresh = async (video: VkpiKolPoolVideoRow) => {
    const evidenceId = Number(video.evidence_id ?? video.id) || 0;
    if (!apiToken || !poolId || !evidenceId || paidActionsReadOnly || refreshingEvidence.has(evidenceId) || queuedRefreshEvidence.has(evidenceId)) return;
    const target = { ...targetRef.current };
    setRefreshingEvidence((prev) => new Set(prev).add(evidenceId));
    setMsg("metrics", null);
    try {
      const resp = await refreshMyKolVideoMetrics(apiToken, target.poolId, evidenceId);
      if (!targetIsCurrent(target)) return;
      const status = String(resp?.status || "");
      if (status !== "queued" && status !== "already_queued") {
        setMsg("metrics", { text: `指标刷新未获服务端确认（#${evidenceId}）：${status || "未知状态"}`, tone: "error" });
        return;
      }
      setQueuedRefreshEvidence((prev) => new Set(prev).add(evidenceId));
      setMsg("metrics", {
        text: `${status === "already_queued" ? "指标刷新已在队列中" : "指标刷新已排队"}（#${evidenceId}）；完成后再次打开详情可查看最新快照趋势。`,
        tone: "info",
      });
      void recovery.refresh();
    } catch (err) {
      if (targetIsCurrent(target)) setMsg("metrics", { text: videoWriteError(err, `指标刷新（#${evidenceId}）`), tone: "error" });
    } finally {
      if (targetIsCurrent(target)) {
        setRefreshingEvidence((prev) => {
          const next = new Set(prev);
          next.delete(evidenceId);
          return next;
        });
      }
    }
  };

  const enqueueOne = async (video: VkpiKolPoolVideoRow) => {
    const eid = Number(video.evidence_id ?? video.id);
    if (!apiToken || !eid || paidActionsReadOnly || busyKeys.has(`deep:${eid}`)) return;
    setBusy(`deep:${eid}`, true);
    try {
      const { enqueueVideoAnalysis } = await import("../../../../services/vkpi/kolPool-api");
      const resp = await enqueueVideoAnalysis(apiToken, item.poolId, eid);
      const status = String(resp?.status || "");
      if (status === "queued" || status === "already_queued" || status === "already_analyzed") {
        setQueuedEvidence((prev) => new Set(prev).add(eid));
        setMsg("deep", { text: status === "already_analyzed" ? `该条已有深析结果(#${eid})。` : `已入队深析(#${eid},思考中泳道)。`, tone: "info" });
        void recovery.refresh();
      } else {
        setMsg("deep", { text: `深析入队被拒(#${eid}):${String(resp?.reason || resp?.message || status || "原因见泳道").slice(0, 100)}`, tone: "error" });
      }
    } catch (err) {
      setMsg("deep", { text: `深析入队失败(#${eid}):${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy(`deep:${eid}`, false);
    }
  };

  const enqueueKeyframeQa = (video: VkpiKolPoolVideoRow) => {
    const eid = Number(video.evidence_id ?? video.id) || 0;
    const target = { ...targetRef.current };
    return runKeyframeQaAction(video, {
      apiToken, target, readOnly: paidActionsReadOnly,
      isCurrent: targetIsCurrent, isBusy: () => !eid || busyKeys.has(`qa:${eid}`),
      setBusy: (id, on) => setBusy(`qa:${id}`, on),
      setReceipt: (receipt) => setMsg("qa", receipt), refresh: () => { void recovery.refresh(); }, writeError: videoWriteError,
    });
  };

  const runDeepBatch = async () => {
    if (!apiToken || !unanalyzed.length || paidActionsReadOnly || busyKeys.has("deepBatch")) return;
    setBusy("deepBatch", true);
    const batch = unanalyzed.slice(0, 5);
    let queued = 0;
    try {
      const { enqueueVideoAnalysis } = await import("../../../../services/vkpi/kolPool-api");
      for (const video of batch) {
        try {
          await enqueueVideoAnalysis(apiToken, item.poolId, Number(video.evidence_id ?? video.id));
          queued += 1;
        } catch {
          /* 单条失败不阻断(已入队/预算拒绝等),计数如实 */
        }
      }
      const rest = unanalyzed.length - batch.length;
      setMsg(
        "deep",
        queued > 0
          ? { text: `已入队 ${queued}/${batch.length} 条视频深析(思考中泳道)${rest > 0 ? `;剩 ${rest} 条未析,本批析完再点继续(配额保护)` : ""}。`, tone: "info" }
          : { text: `视频深析 ${queued}/${batch.length} 条入队成功——可能已在队列或被预算闸拒绝,请看泳道后重试。`, tone: "error" },
      );
    } catch {
      setMsg("deep", { text: "深析入队失败——请重试或看泳道。", tone: "error" });
    } finally {
      setBusy("deepBatch", false);
    }
  };

  // 评论采集:job 终态轮询回执(gone ≠ done 绝不写 ✓ —— PoolEvidenceContent.helpers 同套)。
  const runCommentsCollect = async () => {
    if (!apiToken || paidActionsReadOnly || busyKeys.has("comments")) return;
    setBusy("comments", true);
    try {
      const { enqueueKolPoolCommentsCollect } = await import("../../../../services/vkpi/kolPool-api");
      const resp = await enqueueKolPoolCommentsCollect(apiToken, item.poolId);
      setMsg("comments", { text: String(resp?.status) === "already_queued" ? "评论采集已在队列中——完成后此处自动回执。" : "评论采集已入队(泳道「评论采集」)——完成后此处自动回执。", tone: "info" });
      const jobId = Number((resp as Record<string, unknown>).job_id) || 0;
      if (!jobId) {
        setBusy("comments", false);
        setMsg("comments", missingJobIdReceipt("评论采集"));
        return;
      }
      const terminal = await waitJobTerminal(apiToken, jobId, cancelledRef);
      setBusy("comments", false);
      if (!terminal || cancelledRef.current) return;
      const receipt = commentsTerminalReceipt(terminal);
      if (receipt.refresh) void recovery.reload();
      setMsg("comments", receipt);
    } catch (err) {
      setBusy("comments", false);
      setMsg("comments", { text: errText(err, "评论采集入队失败"), tone: "error" });
    }
  };

  // 入项目:POST /projects/{id}/kols(真实返回才回执;完成触发父级聚合刷新)。
  const runAddToProject = async () => {
    if (!apiToken || busyKeys.has("project")) return;
    if (!projId) return setMsg("project", { text: "请先在下拉里选择目标项目", tone: "error" });
    setBusy("project", true);
    try {
      const { addKolsToProject } = await import("../../../../services/vkpi/projects-api");
      const result = await addKolsToProject(apiToken, projId, [String(item.poolId)]);
      const inserted = Number(result.inserted || 0);
      const skipped = Number(result.skipped_existing || 0);
      const missing = Array.isArray(result.missing_kol_pool_ids) ? result.missing_kol_pool_ids.length : 0;
      if (inserted > 0) {
        setMsg("project", { text: "已新增到项目——「进行中」状态随聚合刷新落位。", tone: "ok" });
        window.dispatchEvent(new CustomEvent("vkpi:projects-changed"));
        window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
        onActionDone?.();
      } else if (skipped > 0 && missing === 0) {
        window.dispatchEvent(new CustomEvent("vkpi:favorites-changed"));
        setMsg("project", { text: "该 KOL 已在目标项目中，本次没有重复写入。", tone: "info" });
      } else {
        setMsg("project", { text: missing ? "入项目失败：该 KOL 已不存在，请刷新列表。" : "入项目失败：服务端未确认写入。", tone: "error" });
      }
    } catch (err) {
      setMsg("project", { text: `入项目失败:${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy("project", false);
    }
  };

  // 受众画像:audience-stats/refresh(status:error 如实红;其余只报「已受理」)。
  const runAudience = async () => {
    if (!apiToken || paidActionsReadOnly || busyKeys.has("audience")) return;
    setBusy("audience", true);
    try {
      const { refreshAudienceStats } = await import("../../../../services/vkpi/kolPool-api");
      const resp = await refreshAudienceStats(apiToken, item.poolId);
      const status = String(resp?.status || "");
      if (status === "error") setMsg("audience", { text: `受众画像失败:${String(resp?.reason || "原因见泳道").slice(0, 100)}`, tone: "error" });
      else setMsg("audience", { text: `受众画像已受理(${status || "ok"}${resp?.sample_size ? ` · 样本 ${resp.sample_size}` : ""})——结果以后端计算为准。`, tone: "info" });
    } catch (err) {
      setMsg("audience", { text: `受众画像失败:${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy("audience", false);
    }
  };

  // 释放认领:viewer-context 真值(can_release)+ /claims/{id}/release;成功后重取上下文。
  const runReleaseClaim = async () => {
    if (!apiToken || !viewer?.canRelease || busyKeys.has("claim")) return;
    if (!window.confirm(`确认释放对「${humanName}」的认领?释放后该 KOL 可被其他成员认领。`)) return;
    setBusy("claim", true);
    try {
      const { releaseKolClaim } = await import("../../../../services/vkpi/kol-api");
      await releaseKolClaim(apiToken, viewer.claimId);
      setMsg("claim", { text: `已释放「${humanName}」的认领(端点确认)。`, tone: "ok" });
      setViewerTick((t) => t + 1);
      onActionDone?.();
    } catch (err) {
      setMsg("claim", { text: `释放失败:${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy("claim", false);
    }
  };

  // 空态深爬:enqueue-profile-deep-crawl(只报入队,不冒充完成)。
  const runDeepCrawl = async () => {
    if (!apiToken || item.isShared || !item.profileUrl || busyKeys.has("crawl")) return;
    setBusy("crawl", true);
    try {
      const { enqueueKolProfileDeepCrawl } = await import("../../../../services/vkpi/kolPool-api");
      const resp = await enqueueKolProfileDeepCrawl(apiToken, item.profileUrl, item.poolId);
      setMsg("crawl", { text: String((resp as Record<string, unknown>)?.status) === "already_queued" ? "深爬已在队列中——完成后重开本弹窗刷新。" : "已入队深爬(泳道可见进度)——完成后重开本弹窗刷新。", tone: "info" });
    } catch (err) {
      setMsg("crawl", { text: `深爬入队失败:${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy("crawl", false);
    }
  };

  if (!item) return null;
  // 【M5】溯源身份跳:档案卡 → KOL 档案页(sessionStorage 传 kol_pool_id + vkpi:open-kol-profile 事件管道,MarketVoice jumpIdentity / CockpitApp 既有监听同口径)
  const openKolProfile = () => { try { window.sessionStorage.setItem("vkpi:kol-profile-id", String(item.poolId)); } catch { /* 存不进不阻断,事件管道仍切页 */ } window.dispatchEvent(new CustomEvent("vkpi:open-kol-profile")); };
  const srcRows: Array<[string, string]> = [
    ["库记录", `vkpi_kol_pool #${item.poolId}(收藏行 vkpi_kol_pool_favorites / 共享行 vkpi_kol_pool_members)`],
    ["追踪链", "GOAFFPRO kol/{id}/link(读/建同端点;优惠码与佣金 PATCH 实时推回总台,销售经链/码归因该 KOL)"],
    ["合作结果", "行内 assignments 真阶段 × Projects 板块同一份映射(曝光 views / 证据计数);未回填读数如实显 —"],
    ["Viltrox 判据", "项目关联优先 / 深析结构化证据 present(画面·字幕·口播) / 标题明确品牌词 / 证据 absent / 其余待深析；无证据不硬贴"],
    ["视频", "GET /kol-pool/{id}/videos · vkpi_kol_video_evidence 当前已采集窗口(limit 200,不等于平台频道全量)"],
    ["播放口径", "view_count 点时实测(抓取时刻读数,非时序);NULL=未实测 ≠ 0 播放,合计已剔除并注明条数"],
    ["Fit 分", "viltrox_fit_score 只读展示(评分公式永不进前端,零回写)"],
    ["认领真值", "viewer-context 端点(vkpi_kol_claims)——行级「已认领」徽为平台+名称桥接提示"],
  ];
  return (
    <ModalShell
      title={`KOL 详情 · ${platformBadge(item.platform)}`}
      sub={<>{item.isShared ? `来自 ${item.sharedByName || "成员"} 的共享(只读可见)` : "我的收藏"} · 入库 {formatLocal(item.createdAt, { year: "numeric" })}(按浏览器时区) · 内容区可向下滚动到底</>}
      onClose={onClose}
      maxWidth="max-w-[760px]"
    >
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <button type="button" className={NAV_BTN} disabled={index <= 0} onClick={() => onNav(index - 1)}>‹ 上一条</button>
        <span className="font-mono text-[10.5px] font-bold text-accent">#{index + 1} / {total}</span>
        <button type="button" className={NAV_BTN} disabled={index >= total - 1} onClick={() => onNav(index + 1)}>下一条 ›</button>
        <button type="button" className={NAV_BTN} onClick={onClose}>≡ 回列表</button>
        <span className="ml-auto font-mono text-[9px] text-muted">↑↓ 连续翻 · ↓ 滚动查看完整内容</span>
      </div>

      {/* 档案卡:头像/名称/平台/粉丝/Fit(只读)/状态徽 + SrcChip 溯源 */}
      <div className="mb-[22px] rounded-[11px] border border-line bg-panel px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-2.5">
          <KolAvatar row={item} size="detail" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-[14px] font-semibold text-ink">{humanName}</span>
              {publicHandle ? <span className="text-[11px] text-muted">{publicHandle}</span> : null}
              <span className="rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[8.5px] font-semibold text-ink-2">{platformBadge(item.platform)}</span>
              {item.isShared ? <span className={`${MINI_BADGE} border-accent-2 text-accent-2`}>共享</span> : <span className={`${MINI_BADGE} border-line text-muted`}>收藏</span>}
              {viewer?.claimId ? (
                <span className={`${MINI_BADGE} border-good bg-good-soft text-good`} title={viewer.expiresAt ? `认领至 ${formatLocal(viewer.expiresAt)}` : "已认领"}>已认领{viewer.staffName ? ` · ${viewer.staffName}` : ""}</span>
              ) : null}
              {item.projects.length > 0 ? <span className={`${MINI_BADGE} border-accent bg-accent-soft text-accent`}>进行中 ×{item.projects.length}</span> : null}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3 font-mono text-[10px] text-muted">
              <span>粉丝 {item.followers != null ? item.followers.toLocaleString() : "—"}</span>
              <span title="只读展示,评分公式不进前端">Fit {item.fit != null ? Math.round(item.fit) : "未评分"}</span>
              {item.country ? <span>{item.country}</span> : null}
              {item.profileUrl ? (
                <a href={item.profileUrl} target="_blank" rel="noopener noreferrer" className="text-accent transition-colors hover:text-accent-hover" onClick={(ev) => ev.stopPropagation()}>主页 ↗</a>
              ) : null}
              <button type="button" className="text-accent transition-colors hover:text-accent-hover" title="跳到 KOL 档案页(同一库记录直达,跨板块)" onClick={openKolProfile}>打开 KOL 档案 →</button>
            </div>
          </div>
          <SrcChip label={`vkpi_kol_pool #${item.poolId}`} rows={srcRows} />
        </div>
      </div>

      {/* 显式最近内容订阅:登记态、调度态、最近成功三层分开，绝不把 active 写成已抓取。 */}
      <KolContentMonitoringSection
        apiToken={apiToken}
        kolPoolId={item.poolId}
        paidActionsReadOnly={paidActionsReadOnly}
        paidActionsReadOnlyHint={paidActionsReadOnlyHint}
      />

      {/* 分区②:追踪链(GOAFFPRO 共享件原样内嵌 —— 生成/复制/优惠码/佣金调整功能零改动) */}
      <GoaffproTrackSection apiToken={apiToken} kolPoolId={item.poolId} readOnly={item.isShared} />

      {/* 分区③:合作项目结果(assignments 真阶段 × Projects 板块同一份映射的曝光/证据) */}
      <CoopResultsSection assignments={item.projects} projects={projects} />

      {/* 分区③b:用过的镜头(深析内容里点名并对上产品目录的 Viltrox 产品;无深析/无镜头如实说明) */}
      <div className="mb-[22px]">
        <SectionLabel>用过的镜头{lensUsage?.coverage?.analysed_videos ? ` · 已深析 ${lensUsage.coverage.analysed_videos} 条` : ""}</SectionLabel>
        {lensUsage == null ? (
          <div className="py-2 text-[11.5px] text-muted">镜头整理读取中…</div>
        ) : (lensUsage.lenses || []).length === 0 && (lensUsage.unresolved || []).length === 0 ? (
          <div className="text-[11.5px] text-muted">
            {lensUsage.empty_reason === "no_analysed_videos" ? "该 KOL 还没有已深析的视频——发起「视频深析入队」后,这里会列出深析内容里出镜的镜头。" : lensUsage.coverage?.unscanned_videos ? `已深析 ${lensUsage.coverage.analysed_videos ?? 0} 条,镜头清单尚未整理(后台回填未跑到)。` : "已深析内容里没有点名 Viltrox 镜头——如实空。"}
          </div>
        ) : (
          <KolLensUsageList lenses={lensUsage.lenses || []} unresolved={lensUsage.unresolved || []} labels={lensUsage.modality_labels} />
        )}
      </div>

      {/* 分区④:已采集内容——品牌关系筛选、五种排序与覆盖率住 libdetail。 */}
      <div className="mb-[22px]">
        <SectionLabel>已采集内容 · Viltrox 筛查</SectionLabel>
        <TrackExistingVideoForm
          item={item}
          apiToken={apiToken}
          collectedUrlVideos={collectedUrlVideos}
          trackUrl={trackUrl}
          setTrackUrl={(value) => { if (pendingDetectedRef.current && value !== String(pendingDetectedRef.current.video.content_url || "").trim()) { pendingDetectedRef.current = null; dataWatchInteractionEpoch.current += 1; setTrackSkuInput(""); } setTrackUrl(value); }}
          trackSkuInput={trackSkuInput}
          setTrackSkuInput={setTrackSkuInput}
          trackBusy={trackBusy || Boolean(pendingDetectedRef.current && dataWatchEvidence.has(Number(pendingDetectedRef.current.video.evidence_id ?? pendingDetectedRef.current.video.id) || 0))}
          confirmDetected={Boolean(pendingDetectedRef.current)}
          paidActionsReadOnly={paidActionsReadOnly}
          paidActionsReadOnlyHint={paidActionsReadOnlyHint}
          onSubmit={() => { void runTrackVideo(); }}
          trackingReceipt={msgs.tracking || null}
          crawlReceipt={msgs.crawl || null}
          crawlBusy={busyKeys.has("crawl")}
          onDeepCrawl={runDeepCrawl}
          onOpenProfile={openKolProfile}
          skuInputRef={skuInputRef}
        />
        {videos == null ? (
          <div className="py-5 text-center text-[12px] text-muted">视频读取中…</div>
        ) : videosError ? (
          <div className="rounded-lg border border-crit bg-crit-soft px-3 py-2 text-[12px] text-crit">
            <div className="font-semibold">视频读取失败</div>
            <div className="mt-0.5 text-[11px]">{videosError}</div>
          </div>
        ) : loaded.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line-strong px-3.5 py-4 text-center text-[12.5px] leading-5 text-muted">
            暂无已采集内容——可发起补采。请使用上方“账号补采 / 深爬”入口；缺少主页时先打开 KOL 档案。
          </div>
        ) : (
          /* libdetail 不改签名:视频卡的一键「数据关注」经 context 供给(VideoTrackActions 回落消费) */
          <DataWatchContext.Provider value={{ onDataWatch: (video) => { void runDataWatch(video); }, busyEvidence: dataWatchEvidence }}>
            <KolVideoSection
              videos={loaded}
              queuedEvidence={queuedEvidence}
              busyKeys={busyKeys}
              onEnqueueOne={enqueueOne}
              onEnqueueKeyframeQa={(video) => { void enqueueKeyframeQa(video); }}
              refreshingEvidence={refreshingEvidence}
              queuedRefreshEvidence={queuedRefreshEvidence}
              onRefreshMetrics={runMetricRefresh}
              trackingEvidence={trackingEvidence}
              onTrackVideo={(video) => { void runTrackVideo({ video }); }}
              onLinkSku={linkSkuFromCard}
              hasMore={recovery.hasMore}
              loadingMore={recovery.loadingMore}
              onLoadMore={() => { void recovery.loadMore(); }}
              total={recovery.total}
              paidActionsReadOnly={paidActionsReadOnly}
              paidActionsReadOnlyHint={paidActionsReadOnlyHint}
            />
          </DataWatchContext.Provider>
        )}
        <RecoveryPollingLine polling={recovery.polling} paused={recovery.pollPaused} onResume={recovery.resumePolling} />
        <ReceiptLine msg={msgs.metrics || null} />
        <ReceiptLine msg={msgs.qa || null} />
      </div>

      {/* 闭环动作排:入项目 / 受众画像 / 深析入队 / 采集评论 / 释放认领(全真端点回执) */}
      <div className="border-t border-line pt-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="入项目目标" value={projId} onChange={(ev) => setProjId(ev.target.value)} className={FIELD}>
            <option value="">选择项目…</option>
            {projects.filter((project) => Number.isFinite(Number(project.id))).map((project) => (
              <option key={project.id} value={String(project.id)}>{project.campaign || `项目 ${project.id}`}</option>
            ))}
          </select>
          <button type="button" className={ACT_BTN} disabled={busyKeys.has("project")} onClick={runAddToProject}>{busyKeys.has("project") ? "入项目中…" : "入项目"}</button>
          <button type="button" className={ACT_BTN} disabled={paidActionsReadOnly || busyKeys.has("audience")} title={paidActionsReadOnly ? paidActionsReadOnlyHint : "入队受众画像刷新"} onClick={runAudience}>{busyKeys.has("audience") ? "受理中…" : "受众画像"}</button>
          <button type="button" className={ACT_BTN} disabled={paidActionsReadOnly || !unanalyzed.length || busyKeys.has("deepBatch")} title={paidActionsReadOnly ? paidActionsReadOnlyHint : unanalyzed.length ? `未析真视频 ${unanalyzed.length} 条,本批前 5(配额保护)` : "无可深析视频"} onClick={runDeepBatch}>
            {busyKeys.has("deepBatch") ? "入队中…" : `视频深析入队${unanalyzed.length ? ` ×${Math.min(5, unanalyzed.length)}` : ""}`}
          </button>
          <button type="button" className={ACT_BTN} disabled={paidActionsReadOnly || !loaded.length || busyKeys.has("comments")} title={paidActionsReadOnly ? paidActionsReadOnlyHint : "入队评论采集(job 终态轮询回执;超出轮询窗只报仍在后台)"} onClick={runCommentsCollect}>
            {busyKeys.has("comments") ? "采集跟踪中…" : "采集评论"}
          </button>
          <button type="button" className={ACT_BTN} disabled={item.isShared || !item.profileUrl || busyKeys.has("crawl")} title={item.isShared ? "共享 KOL 仅可查看，不能发起账号补采" : item.profileUrl ? "重跑账号分析补采最新视频(旧库右栏同款入口;只报入队,泳道可见进度)" : "该 KOL 无主页链接,无法账号分析"} onClick={runDeepCrawl}>
            {busyKeys.has("crawl") ? "入队中…" : "账号分析 · 补采"}
          </button>
          <button type="button" className={ACT_BTN} disabled={!viewer?.canRelease || busyKeys.has("claim")} title={viewer?.canRelease ? "释放本人 active 认领" : "无本人可释放的认领(以认领真值端点为准)"} onClick={runReleaseClaim}>
            {busyKeys.has("claim") ? "释放中…" : "释放认领"}
          </button>
        </div>
        {/* crawl 回执固定住在追踪恢复入口，其他动作仍在本排就近展示。 */}
        {(["project", "audience", "deep", "comments", "claim"] as const).map((key) => (
          <ReceiptLine key={key} msg={msgs[key] || null} />
        ))}
        <div className="mt-1.5 text-right text-[9.5px] text-muted">动作回执以端点真实返回为准 · 后台任务超出轮询窗只报「仍在后台」,绝不冒充完成</div>
      </div>
    </ModalShell>
  );
}
