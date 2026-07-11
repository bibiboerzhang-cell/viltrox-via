import React from "react";
import { SrcChip, RecordPreview } from "../components/provenance";
import { formatLocal } from "../../lib/timeLocal";
import { ModalShell, SectionLabel, Drow, platformBadge } from "./MarketVoicePage.dialogs";
import {
  filterLibraryRows,
  getMyKolPoolVideos,
  isImageKindVideo,
  sortClassifiedVideos,
  summarizeKolVideos,
  V_TIER_LABEL,
  videoRecordRows,
  type KolLibraryRow,
  type LibraryFilter,
  type VContentTier,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import {
  commentsTerminalReceipt,
  missingJobIdReceipt,
  waitJobTerminal,
  type FlowReceipt,
} from "../../pages/myKol/PoolEvidenceContent.helpers";
import type { VkpiProjectRow } from "../../vkpiTypes";

// MY KOL · 弹窗族(M3:库弹窗化 + V 视频筛选;金样板 = MarketVoicePage.dialogs 的
//   FeedListModal/FeedDetailModal 连续翻体验,ModalShell/SectionLabel/Drow 复用零重写)。
//   依赖单向:MyKolBoardPage.modules → 本文件(反向禁止);行件/筛选 chips 住这里供两侧共用。
//   与金样板的一处如实差异:本弹窗族持 apiToken 直调 services(动作端点多,页面层保持瘦身);
//   mock seam 不变 —— 全部网络仍收敛到 services/http.apiFetch 单出口。
// 动作纪律(绝不假成功):回执一律以端点真实返回为准;评论采集走 job 终态轮询(gone ≠ done
//   绝不写 ✓);受众画像/深析/深爬只报「已受理/已入队」。V 三档=派生规则(后端同构),
//   口径只进 SrcChip/记录预览,卡面零技术术语;播放合计只算实测,NULL 条数如实注明。
// 红线:颜色全 token 类零写死色;禁 opacity 修饰类;纯展示绝不写 fit 分 / 不触 rule_v0。

/* ============ V 相关三档徽(cooperation=accent / title_mention=good / undetermined=muted) ============ */
export const V_TIER_META: Record<VContentTier, { label: string; cls: string }> = {
  cooperation: { label: V_TIER_LABEL.cooperation, cls: "border-accent bg-accent-soft text-accent" },
  title_mention: { label: V_TIER_LABEL.title_mention, cls: "border-good bg-good-soft text-good" },
  undetermined: { label: V_TIER_LABEL.undetermined, cls: "border-line text-muted" },
};

const NAV_BTN =
  "rounded-lg border border-line px-2.5 py-1 text-[11px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink disabled:cursor-default disabled:text-muted";
const ACT_BTN =
  "rounded-lg border border-line px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line disabled:hover:bg-transparent";
const CHIP = "rounded-full border px-2.5 py-1 text-[10.5px] transition-colors";
const CHIP_ON = "border-accent bg-accent-soft text-accent";
const CHIP_OFF = "border-line text-muted hover:text-ink";
const MINI_BADGE = "flex-none rounded-[5px] border px-1 py-px text-[8px] font-bold";
const MORE_BTN =
  "mt-2.5 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-2 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft";
const FIELD = "rounded-lg border border-line bg-card px-2 py-1.5 text-[10.5px] text-ink-2 outline-none focus:border-accent";

function toneCls(tone: FlowReceipt["tone"]): string {
  if (tone === "error") return "border-crit bg-crit-soft text-crit";
  if (tone === "ok") return "border-good bg-good-soft text-good";
  return "border-info bg-info-soft text-info";
}

export function ReceiptLine({ msg }: { msg: FlowReceipt | null }) {
  if (!msg) return null;
  return (
    <div role={msg.tone === "error" ? "alert" : "status"} className={`mt-2 rounded-lg border px-3 py-1.5 text-[11px] ${toneCls(msg.tone)}`}>
      {msg.text}
    </div>
  );
}

const errText = (err: unknown, fallback: string) =>
  String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || fallback).slice(0, 100);

/* ============ 库行(卡面 6 条与全量弹窗共用;点行开详情,弹窗内可勾选批量) ============ */
export function KolRowLine({
  row,
  index,
  onOpen,
  selectable = false,
  selected = false,
  onToggleSelect,
}: {
  row: KolLibraryRow;
  index: number;
  onOpen: (i: number) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (poolId: number) => void;
}) {
  const onKey = (ev: React.KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      onOpen(index);
    }
  };
  return (
    <div className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0" role="button" tabIndex={0} onClick={() => onOpen(index)} onKeyDown={onKey}>
      {selectable ? (
        <input type="checkbox" aria-label={`勾选 ${row.name}`} checked={selected} onChange={() => onToggleSelect?.(row.poolId)} onClick={(ev) => ev.stopPropagation()} className="h-3.5 w-3.5 flex-none accent-[var(--ds-accent)]" />
      ) : null}
      {row.avatarUrl ? (
        <img src={row.avatarUrl} alt="" loading="lazy" className="h-[22px] w-[22px] flex-none rounded-full border border-line object-cover" />
      ) : (
        <span className="grid h-[22px] w-[22px] flex-none place-items-center rounded-full border border-line bg-panel text-[10px] text-muted">{(row.name || "?").slice(0, 1).toUpperCase()}</span>
      )}
      <span className="min-w-[42px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">{platformBadge(row.platform)}</span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent">
        {row.name}
        {row.handle && row.handle !== row.name ? <span className="ml-1.5 text-[10px] text-muted">{row.handle}</span> : null}
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
}: {
  filter: LibraryFilter;
  onFilter: (next: LibraryFilter) => void;
  platformOptions: Array<{ platform: string; count: number }>;
  /** board-ext 全库有 V 相关视频的 KOL 总数;null = 聚合未就绪(chip 不带数,不编) */
  vKolCount: number | null;
  /** 负责人筛选(管理层;走服务端 scope 重取,零本地猜);缺省不渲染 */
  staff?: { options: Array<{ id: string; name: string }>; value: string; onChange: (id: string) => void; busy?: boolean };
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={`${CHIP} ${filter.vOnly ? CHIP_ON : CHIP_OFF}`} onClick={() => onFilter({ ...filter, vOnly: true })} title="全库有 V 相关视频的 KOL 总数;列表按「已进项目(合作)」过滤,单条三档判定见详情">
          有 V 视频{vKolCount != null ? ` (${vKolCount.toLocaleString()})` : ""}
        </button>
        <button type="button" className={`${CHIP} ${filter.vOnly ? CHIP_OFF : CHIP_ON}`} onClick={() => onFilter({ ...filter, vOnly: false })}>全部</button>
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
        <div className="mt-1.5 text-[9.5px] text-muted">
          筛选判据=已进项目的合作 KOL{vKolCount != null ? `;${vKolCount.toLocaleString()} 为全库有 V 相关视频的 KOL 总数` : ""} · 单条视频三档判定见详情
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
    let ok = 0, fail = 0, firstError = "";
    try {
      const { addKolsToProject } = await import("../../../../services/vkpi/projects-api");
      for (let i = 0; i < selectedRows.length; i += 1) {
        setNote({ text: `批量入项目中… ${i + 1}/${selectedRows.length}`, tone: "info" });
        try {
          await addKolsToProject(apiToken, projId, [String(selectedRows[i].poolId)]);
          ok += 1;
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
    setNote({ text: `批量入项目完成:成功 ${ok} 个${fail ? ` · 失败 ${fail} 个${firstError ? `(首个原因:${firstError})` : ""}` : ""}`, tone: fail ? "error" : "ok" });
    if (ok) onActionDone?.();
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
    if (selectedRows.length > 10 && !window.confirm(`选中 ${selectedRows.length} 个 KOL,受众画像逐个刷新可能耗时较久,确认继续?`)) return;
    setBusy(true);
    let ok = 0, fail = 0, firstReason = "";
    try {
      const { refreshAudienceStats } = await import("../../../../services/vkpi/kolPool-api");
      for (let i = 0; i < selectedRows.length; i += 1) {
        setNote({ text: `受众画像入队中… ${i + 1}/${selectedRows.length}`, tone: "info" });
        try {
          const resp = await refreshAudienceStats(apiToken, selectedRows[i].poolId);
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
    setNote({ text: `受众画像已受理 ${ok} 个${fail ? ` · 失败 ${fail} 个${firstReason ? `(首个原因:${firstReason})` : ""}` : ""}——结果以后端计算为准`, tone: fail ? "error" : "ok" });
  };

  const projectOptions = projects.filter((project) => Number.isFinite(Number(project.id)));
  return (
    <ModalShell title="KOL 库 · 全量" sub={`筛出 ${rows.length} 条 / 在库 ${totalAll} 条 · 点单条看详情(详情内 ↑↓ 连续翻)`} onClose={onClose} maxWidth="max-w-[760px]">
      <input type="search" value={filter.query} onChange={(ev) => onFilter({ ...filter, query: ev.target.value })} placeholder="搜索名称 / handle…" aria-label="搜索 KOL" className="mb-2.5 w-full rounded-[10px] border border-line bg-card px-3 py-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent" />
      <div className="mb-2.5">
        <LibraryChips filter={filter} onFilter={onFilter} platformOptions={platformOptions} vKolCount={vKolCount} staff={staff} />
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
        <button type="button" className={ACT_BTN} disabled={busy || !selectedRows.length} onClick={runBatchAudience}>批量受众画像</button>
      </div>
      <ReceiptLine msg={note} />
      <SectionLabel>库内 KOL</SectionLabel>
      {rows.length === 0 ? (
        <div className="px-3 py-4 text-center text-[12px] text-muted">该筛选组合下 0 条——诚实空,不编行。</div>
      ) : (
        shown.map((row, i) => <KolRowLine key={row.poolId} row={row} index={i} onOpen={onOpenDetail} selectable selected={selected.has(row.poolId)} onToggleSelect={toggleSelect} />)
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
  const total = rows.length;
  const cancelledRef = React.useRef(false);
  React.useEffect(() => {
    cancelledRef.current = false;
    return () => { cancelledRef.current = true; };
  }, [item?.poolId]);

  // ↑↓(以及 ←→)方向键连续翻(金样板同款);Escape 交给 ModalShell 栈。
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") { ev.preventDefault(); onNav(index + 1); }
      else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") { ev.preventDefault(); onNav(index - 1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, onNav]);

  // 视频区取数:GET /kol-pool/{id}/videos(全部 evidence,limit 200 与详情抽屉同口径)。
  const [videos, setVideos] = React.useState<VkpiKolPoolVideoRow[] | null>(null);
  const [videosError, setVideosError] = React.useState("");
  const [videosTick, setVideosTick] = React.useState(0);
  React.useEffect(() => {
    if (!apiToken || !item?.poolId) return;
    let alive = true;
    setVideos(null);
    setVideosError("");
    getMyKolPoolVideos(apiToken, item.poolId, 200)
      .then((resp) => { if (alive) setVideos(Array.isArray(resp.items) ? resp.items : []); })
      .catch((err: unknown) => { if (!alive) return; setVideos([]); setVideosError(errText(err, "视频读取失败")); });
    return () => { alive = false; };
  }, [apiToken, item?.poolId, videosTick]);

  // 观看者上下文(认领真值):GET /my-kol/{id}/viewer-context —— 释放按钮据 can_release;
  // 读取失败 → 释放按钮保持不可用(诚实降级,不猜)。
  const [viewer, setViewer] = React.useState<{ claimId: string; staffName: string; expiresAt: string; canRelease: boolean } | null>(null);
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
        setViewer(claim && claim.id != null ? { claimId: String(claim.id), staffName: String(claim.staff_name || ""), expiresAt: String(claim.expires_at || ""), canRelease: Boolean(claim.can_release) } : null);
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [apiToken, item?.poolId, viewerTick]);

  // 动作回执槽(每流独立,失败不被后续覆盖)+ 每条切换全部清空。
  const [msgs, setMsgs] = React.useState<Record<string, FlowReceipt | null>>({});
  const [busyKeys, setBusyKeys] = React.useState<Set<string>>(new Set());
  const [queuedEvidence, setQueuedEvidence] = React.useState<Set<number>>(new Set());
  const [vOnly, setVOnly] = React.useState(false);
  const [sortBy, setSortBy] = React.useState<"time" | "views">("time");
  const [recEvidence, setRecEvidence] = React.useState<VkpiKolPoolVideoRow | null>(null);
  const [projId, setProjId] = React.useState("");
  React.useEffect(() => {
    setMsgs({}); setQueuedEvidence(new Set()); setVOnly(false); setSortBy("time"); setRecEvidence(null); setProjId("");
  }, [item?.poolId]);
  const setMsg = (key: string, msg: FlowReceipt | null) => setMsgs((prev) => ({ ...prev, [key]: msg }));
  const setBusy = (key: string, on: boolean) =>
    setBusyKeys((prev) => { const next = new Set(prev); if (on) next.add(key); else next.delete(key); return next; });

  const loaded = videos || [];
  const summary = React.useMemo(() => summarizeKolVideos(loaded), [loaded]);
  const { classified, unmeasuredCount, viewsTotal, vRelatedCount, analyzedCount, unanalyzed } = summary;
  const shownVideos = React.useMemo(() => sortClassifiedVideos(classified, vOnly, sortBy), [classified, vOnly, sortBy]);

  // 单条深析入队(「未判定」一键深析同用):端点真实返回才标「已入队」。
  const enqueueOne = async (video: VkpiKolPoolVideoRow) => {
    const eid = Number(video.evidence_id ?? video.id);
    if (!apiToken || !eid || busyKeys.has(`deep:${eid}`)) return;
    setBusy(`deep:${eid}`, true);
    try {
      const { enqueueVideoAnalysis } = await import("../../../../services/vkpi/kolPool-api");
      const resp = await enqueueVideoAnalysis(apiToken, item.poolId, eid);
      const status = String(resp?.status || "");
      if (status === "queued" || status === "already_queued" || status === "already_analyzed") {
        setQueuedEvidence((prev) => new Set(prev).add(eid));
        setMsg("deep", { text: status === "already_analyzed" ? `该条已有深析结果(#${eid})。` : `已入队深析(#${eid},思考中泳道)。`, tone: "info" });
      } else {
        setMsg("deep", { text: `深析入队被拒(#${eid}):${String(resp?.reason || resp?.message || status || "原因见泳道").slice(0, 100)}`, tone: "error" });
      }
    } catch (err) {
      setMsg("deep", { text: `深析入队失败(#${eid}):${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy(`deep:${eid}`, false);
    }
  };

  // 批量深析:未析真视频前 5 条(配额保护,PoolEvidenceContent 同款限批)。
  const runDeepBatch = async () => {
    if (!apiToken || !unanalyzed.length || busyKeys.has("deepBatch")) return;
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
    if (!apiToken || busyKeys.has("comments")) return;
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
      if (receipt.refresh) setVideosTick((t) => t + 1);
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
      await addKolsToProject(apiToken, projId, [String(item.poolId)]);
      setMsg("project", { text: "已入项目(端点确认)——「进行中」状态随下次聚合刷新落位。", tone: "ok" });
      onActionDone?.();
    } catch (err) {
      setMsg("project", { text: `入项目失败:${errText(err, "请重试")}`, tone: "error" });
    } finally {
      setBusy("project", false);
    }
  };

  // 受众画像:audience-stats/refresh(status:error 如实红;其余只报「已受理」)。
  const runAudience = async () => {
    if (!apiToken || busyKeys.has("audience")) return;
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
    if (!window.confirm(`确认释放对「${item.name}」的认领?释放后该 KOL 可被其他成员认领。`)) return;
    setBusy("claim", true);
    try {
      const { releaseKolClaim } = await import("../../../../services/vkpi/kol-api");
      await releaseKolClaim(apiToken, viewer.claimId);
      setMsg("claim", { text: `已释放「${item.name}」的认领(端点确认)。`, tone: "ok" });
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
    if (!apiToken || !item.profileUrl || busyKeys.has("crawl")) return;
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
  const srcRows: Array<[string, string]> = [
    ["库记录", `vkpi_kol_pool #${item.poolId}(收藏行 vkpi_kol_pool_favorites / 共享行 vkpi_kol_pool_members)`],
    ["V 三档判据", "cooperation=evidence.project_id 非空 / title_mention=标题含 viltrox(不分大小写)/ 其余=未判定 —— 派生规则非采集字段(classify_v_content 后端同口径)"],
    ["视频", "GET /kol-pool/{id}/videos · vkpi_kol_video_evidence 全量(limit 200)"],
    ["播放口径", "view_count 点时实测(抓取时刻读数,非时序);NULL=未实测 ≠ 0 播放,合计已剔除并注明条数"],
    ["Fit 分", "viltrox_fit_score 只读展示(评分公式永不进前端,零回写)"],
    ["认领真值", "viewer-context 端点(vkpi_kol_claims)——行级「已认领」徽为平台+名称桥接提示"],
  ];
  return (
    <ModalShell
      title={`KOL 详情 · ${platformBadge(item.platform)}`}
      sub={<>{item.isShared ? `来自 ${item.sharedByName || "成员"} 的共享(只读可见)` : "我的收藏"} · 入库 {formatLocal(item.createdAt, { year: "numeric" })}(按浏览器时区)</>}
      onClose={onClose}
      maxWidth="max-w-[760px]"
    >
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <button type="button" className={NAV_BTN} disabled={index <= 0} onClick={() => onNav(index - 1)}>‹ 上一条</button>
        <span className="font-mono text-[10.5px] font-bold text-accent">#{index + 1} / {total}</span>
        <button type="button" className={NAV_BTN} disabled={index >= total - 1} onClick={() => onNav(index + 1)}>下一条 ›</button>
        <button type="button" className={NAV_BTN} onClick={onClose}>≡ 回列表</button>
        <span className="ml-auto font-mono text-[9px] text-muted">↑↓ 方向键连续翻</span>
      </div>

      {/* 档案卡:头像/名称/平台/粉丝/Fit(只读)/状态徽 + SrcChip 溯源 */}
      <div className="mb-[22px] rounded-[11px] border border-line bg-panel px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-2.5">
          {item.avatarUrl ? (
            <img src={item.avatarUrl} alt="" className="h-9 w-9 flex-none rounded-full border border-line object-cover" />
          ) : (
            <span className="grid h-9 w-9 flex-none place-items-center rounded-full border border-line bg-card text-[14px] text-muted">{(item.name || "?").slice(0, 1).toUpperCase()}</span>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-[14px] font-semibold text-ink">{item.name}</span>
              {item.handle && item.handle !== item.name ? <span className="text-[11px] text-muted">{item.handle}</span> : null}
              <span className="rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[8.5px] font-semibold text-ink-2">{platformBadge(item.platform)}</span>
              {item.isShared ? <span className={`${MINI_BADGE} border-accent-2 text-accent-2`}>共享</span> : <span className={`${MINI_BADGE} border-line text-muted`}>收藏</span>}
              {viewer ? (
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
            </div>
          </div>
          <SrcChip label={`vkpi_kol_pool #${item.poolId}`} rows={srcRows} />
        </div>
      </div>

      {/* 视频区(裁决③):全部过往视频网格 + V 三档徽 + 仅V开关 + 排序 + 诚实小结 */}
      <div className="mb-[22px]">
        <SectionLabel>过往视频 · 全量</SectionLabel>
        {videos == null ? (
          <div className="py-5 text-center text-[12px] text-muted">视频读取中…</div>
        ) : videosError ? (
          <div className="rounded-lg border border-crit bg-crit-soft px-3 py-2 text-[12px] text-crit">
            <div className="font-semibold">视频读取失败</div>
            <div className="mt-0.5 text-[11px]">{videosError}</div>
          </div>
        ) : loaded.length === 0 ? (
          <div className="rounded-xl border border-dashed border-line-strong px-3.5 py-4 text-center text-[12px] text-muted">
            暂无采集视频——可发起深爬。
            <div className="mt-2">
              <button type="button" className={ACT_BTN} disabled={!item.profileUrl || busyKeys.has("crawl")} title={item.profileUrl ? "入队账号深爬(泳道可见进度)" : "该 KOL 无主页链接,无法深爬"} onClick={runDeepCrawl}>
                {busyKeys.has("crawl") ? "入队中…" : "发起深爬"}
              </button>
            </div>
            <ReceiptLine msg={msgs.crawl || null} />
          </div>
        ) : (
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted">
              <span>
                {loaded.length} 条视频 · 实测播放合计 {viewsTotal.toLocaleString()}
                {unmeasuredCount > 0 ? `(${unmeasuredCount} 条未实测已剔除)` : ""} · V 相关 {vRelatedCount} 条 · 已深析 {analyzedCount} 条
              </span>
              <span className="ml-auto flex items-center gap-1.5">
                <button type="button" className={`${CHIP} ${vOnly ? CHIP_ON : CHIP_OFF}`} onClick={() => setVOnly((v) => !v)} title="只看合作产出与标题提及V(未判定隐藏)">仅 V 相关</button>
                <button type="button" className={`${CHIP} ${sortBy === "time" ? CHIP_ON : CHIP_OFF}`} onClick={() => setSortBy("time")}>按时间</button>
                <button type="button" className={`${CHIP} ${sortBy === "views" ? CHIP_ON : CHIP_OFF}`} onClick={() => setSortBy("views")}>按播放</button>
              </span>
            </div>
            {shownVideos.length === 0 ? (
              <div className="px-3 py-4 text-center text-[12px] text-muted">该筛选下 0 条(仅 V 相关开启)——诚实空。</div>
            ) : (
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
                {shownVideos.map(({ video, tier }) => {
                  const eid = Number(video.evidence_id ?? video.id) || 0;
                  const thumb = String(video.cached_thumbnail_url || video.thumbnail_url || "");
                  const title = String(video.title || video.video_title || "未命名视频");
                  const meta = V_TIER_META[tier];
                  return (
                    <div key={eid} className="overflow-hidden rounded-[11px] border border-line bg-panel">
                      <div className="grid h-[84px] w-full place-items-center overflow-hidden bg-card">
                        {thumb ? <img src={thumb} alt="" loading="lazy" className="h-full w-full object-cover" /> : <span className="text-[16px] text-muted">▶</span>}
                      </div>
                      <div className="px-2.5 py-2">
                        <div className="truncate text-[11px] text-ink" title={title}>{title}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[9.5px] text-muted">
                          <span title={video.view_count == null ? "未实测(≠ 0 播放)" : "播放(点时实测)"}>▶ {video.view_count != null ? Number(video.view_count).toLocaleString() : "未实测"}</span>
                          <span>♥ {Number(video.like_count ?? 0).toLocaleString()}</span>
                          <span>💬 {Number(video.comment_count ?? 0).toLocaleString()}</span>
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1">
                          <span className={`rounded-[5px] border px-1 py-px text-[8px] font-bold ${meta.cls}`}>{meta.label}</span>
                          {video.has_final_v1_cache ? <span className={`${MINI_BADGE} border-good bg-good-soft text-good`}>已深析</span> : null}
                          {video.content_url ? (
                            <a className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none" href={String(video.content_url)} target="_blank" rel="noopener noreferrer" title="直跳原帖" onClick={(ev) => ev.stopPropagation()}>↗</a>
                          ) : null}
                          <button type="button" className="rounded-[5px] border border-line px-1 py-px font-mono text-[8px] text-muted transition-colors hover:border-accent hover:text-accent" title="库记录预览" onClick={() => setRecEvidence((prev) => ((prev?.evidence_id ?? prev?.id) === eid ? null : video))}>
                            #{eid}
                          </button>
                          {tier === "undetermined" && !video.has_final_v1_cache && !isImageKindVideo(video) ? (
                            <button type="button" className="rounded-[5px] border border-line px-1 py-px text-[8px] text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-default" disabled={queuedEvidence.has(eid) || busyKeys.has(`deep:${eid}`)} title="未判定视频一键入队深析(端点真实返回才标已入队)" onClick={() => enqueueOne(video)}>
                              {queuedEvidence.has(eid) ? "已入队" : busyKeys.has(`deep:${eid}`) ? "入队中…" : "深析"}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {recEvidence ? <RecordPreview title="库记录预览 · 点其他 #id 切换" rows={videoRecordRows(recEvidence)} /> : null}
          </div>
        )}
      </div>

      {/* 合作项目(aggregate 直连 assignments,只读) */}
      {item.projects.length > 0 ? (
        <div className="mb-[22px]">
          <SectionLabel>合作项目 ×{item.projects.length}</SectionLabel>
          {item.projects.slice(0, 6).map((project, i) => (
            <Drow key={`${project.project_id}-${i}`} k={String(project.project_name || `项目 ${project.project_id}`)} v={String(project.stage || "—")} />
          ))}
        </div>
      ) : null}

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
          <button type="button" className={ACT_BTN} disabled={busyKeys.has("audience")} onClick={runAudience}>{busyKeys.has("audience") ? "受理中…" : "受众画像"}</button>
          <button type="button" className={ACT_BTN} disabled={!unanalyzed.length || busyKeys.has("deepBatch")} title={unanalyzed.length ? `未析真视频 ${unanalyzed.length} 条,本批前 5(配额保护)` : "无可深析视频"} onClick={runDeepBatch}>
            {busyKeys.has("deepBatch") ? "入队中…" : `视频深析入队${unanalyzed.length ? ` ×${Math.min(5, unanalyzed.length)}` : ""}`}
          </button>
          <button type="button" className={ACT_BTN} disabled={!loaded.length || busyKeys.has("comments")} title="入队评论采集(job 终态轮询回执;超出轮询窗只报仍在后台)" onClick={runCommentsCollect}>
            {busyKeys.has("comments") ? "采集跟踪中…" : "采集评论"}
          </button>
          <button type="button" className={ACT_BTN} disabled={!viewer?.canRelease || busyKeys.has("claim")} title={viewer?.canRelease ? "释放本人 active 认领" : "无本人可释放的认领(以认领真值端点为准)"} onClick={runReleaseClaim}>
            {busyKeys.has("claim") ? "释放中…" : "释放认领"}
          </button>
        </div>
        {(["project", "audience", "deep", "comments", "claim"] as const).map((key) => (
          <ReceiptLine key={key} msg={msgs[key] || null} />
        ))}
        <div className="mt-1.5 text-right text-[9.5px] text-muted">动作回执以端点真实返回为准 · 后台任务超出轮询窗只报「仍在后台」,绝不冒充完成</div>
      </div>
    </ModalShell>
  );
}

export { filterLibraryRows };
