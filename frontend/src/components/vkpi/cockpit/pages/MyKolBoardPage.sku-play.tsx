import React from "react";
import { ChevronDown, ChevronRight, ExternalLink, RefreshCw } from "lucide-react";
import { EmptyLine, ErrorCard, LoadingLine, PendingCard } from "./MarketVoicePage.modules";
import { formatLocal, relativeFromNow } from "../../lib/timeLocal";
import { ApiResponseError } from "../../../../services/http";
import { platformLabel } from "../../../../services/vkpi/myKolBoard-api";
import {
  fetchSkuPlayOverview,
  skuPlayCountText,
  type SkuPlayGroup,
  type SkuPlayItem,
  type VkpiMyKolSkuPlayOverviewResponse,
} from "../../../../services/vkpi/myKolSkuPlay-api";
import { SKU_PLAY_CHANGED_EVENT, type SkuPlayChangedDetail } from "./MyKolBoardPage.data-watch";

/* ============ MY KOL · 单品播放数据(波 D·C 车道)============
   被「数据关注」登记追踪的视频按单品(SKU)聚合:每个单品一组 —— 组头 = 单品名 +
   视频/红人数 + 累计播放 + Δ7 天 + 最后实测;点组头展开逐视频行(标题直跳原帖、
   KOL+平台、播放/点赞、Δ7 天、追踪状态)。数据 = GET my-kol/sku-play-overview
   (纯读;收藏 ∪ 授权共享口径,员工恒本人,管理层全团队,后端 scope 裁剪)。
   诚实口径:null = 未实测(绝不当 0);Δ 样本不足 = 待积累;端点 404 = 该版本暂无,
   整块如实待接。时间走 timeLocal(浏览器时区),禁硬编码「刚刚/实时」。
   红线:纯读;颜色全 token;零 fit/rule_v0;登记入口在内容墙 / KOL 详情视频卡。 */

function errDetail(err: unknown, fallback: string): string {
  const detail = (err as { detail?: unknown; message?: unknown }) || {};
  return String(detail.detail || detail.message || fallback);
}

function isNotFound(err: unknown): boolean {
  return err instanceof ApiResponseError && err.status === 404;
}

function num(value: unknown): number | null {
  const n = Number(value);
  return value != null && Number.isFinite(n) ? n : null;
}

const TRACKING_LABEL: Record<string, { label: string; cls: string }> = {
  active: { label: "追踪中", cls: "border-good text-good" },
  paused: { label: "已暂停", cls: "border-warn text-warn" },
};

function TrackingChip({ status }: { status: string | undefined }) {
  const key = String(status || "").trim();
  if (!key) return <span className="text-muted">—</span>;
  const known = TRACKING_LABEL[key];
  return (
    <span className={`rounded-[4px] border px-1 py-px text-[8.5px] font-semibold ${known ? known.cls : "border-line text-muted"}`}>
      {known ? known.label : key}
    </span>
  );
}

const LINK_LABEL: Record<string, { label: string; cls: string }> = {
  manual: { label: "员工关联", cls: "border-accent text-accent" },
  confirmed: { label: "已确认", cls: "border-good text-good" },
  detected: { label: "系统检测·待确认", cls: "border-warn text-warn" },
};

function ProductLinkChip({ relation }: { relation: string | undefined }) {
  const key = String(relation || "").trim();
  const known = LINK_LABEL[key];
  return known ? (
    <span className={`rounded-[4px] border px-1 py-px text-[8.5px] font-semibold ${known.cls}`}>{known.label}</span>
  ) : <span className="text-muted">—</span>;
}

/** Δ 播放:正 ↑ 绿 / 负 ↓ 红 / 0 中性;null = 待积累(样本不足不编数)。 */
function DeltaText({ value }: { value: number | null | undefined }) {
  const n = num(value);
  if (n == null) return <span className="font-mono text-muted">待积累</span>;
  const tone = n > 0 ? "text-good" : n < 0 ? "text-crit" : "text-ink";
  const arrow = n > 0 ? "↑" : n < 0 ? "↓" : "";
  return (
    <span className={`font-mono ${tone}`}>
      {arrow}
      {n > 0 ? "+" : ""}
      {n.toLocaleString()}
    </span>
  );
}

/** 最后实测:相对时间(hover 出绝对时间,均按浏览器时区);无成功实测 = 未实测。 */
function MeasuredAt({ ts }: { ts: string | null | undefined }) {
  if (!ts) return <span className="text-muted">未实测</span>;
  const rel = relativeFromNow(ts);
  return (
    <span className="text-muted" title={formatLocal(ts)}>
      {rel || formatLocal(ts)}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="whitespace-nowrap">
      {label} <b className="font-mono text-ink">{value}</b>
    </span>
  );
}

function GroupItemsTable({
  items,
  focusedEvidenceId,
  setEvidenceAnchor,
}: {
  items: SkuPlayItem[];
  focusedEvidenceId: number;
  setEvidenceAnchor: (evidenceId: number, node: HTMLTableRowElement | null) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-[11.5px]">
        <thead>
          <tr className="text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-muted">
            <th className="py-1.5 pr-2 font-semibold">视频</th>
            <th className="py-1.5 pr-2 font-semibold">KOL</th>
            <th className="py-1.5 pr-2 text-right font-semibold">播放</th>
            <th className="py-1.5 pr-2 text-right font-semibold">点赞</th>
            <th className="py-1.5 pr-2 font-semibold">最后实测</th>
            <th className="py-1.5 pr-2 text-right font-semibold">Δ7 天</th>
            <th className="py-1.5 pr-2 font-semibold">SKU 归属</th>
            <th className="py-1.5 font-semibold">状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => {
            const title = row.title || row.content_url || `#${row.evidence_id}`;
            const focused = focusedEvidenceId > 0 && Number(row.evidence_id) === focusedEvidenceId;
            return (
              <tr
                key={row.evidence_id}
                ref={(node) => setEvidenceAnchor(Number(row.evidence_id), node)}
                tabIndex={-1}
                aria-current={focused ? "true" : undefined}
                data-vkpi-sku-play-evidence={row.evidence_id}
                data-vkpi-sku-play-evidence-focus={focused ? "active" : "idle"}
                className={`border-t border-line transition-colors duration-300 ${focused ? "bg-accent-soft outline outline-2 outline-accent outline-offset-[-2px]" : ""}`}
              >
                <td className="max-w-[260px] py-1.5 pr-2">
                  {row.content_url ? (
                    <a
                      href={row.content_url}
                      target="_blank"
                      rel="noreferrer"
                      className="flex min-w-0 items-center gap-1.5 font-semibold text-ink-2 transition-colors hover:text-accent"
                      title={title}
                    >
                      <span className="min-w-0 truncate">{title}</span>
                      <ExternalLink size={10} className="flex-none text-muted" />
                    </a>
                  ) : (
                    <span className="block min-w-0 truncate font-semibold text-ink-2" title={title}>{title}</span>
                  )}
                </td>
                <td className="max-w-[160px] py-1.5 pr-2">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <span className="min-w-0 truncate text-ink-2" title={row.kol_name || undefined}>{row.kol_name || `#${row.kol_pool_id}`}</span>
                    {row.platform ? (
                      <span className="flex-none rounded-[4px] border border-line px-1 py-px text-[8.5px] text-muted">{platformLabel(row.platform)}</span>
                    ) : null}
                  </div>
                </td>
                <td className={`py-1.5 pr-2 text-right font-mono ${row.view_count == null ? "text-muted" : "text-ink"}`}>{skuPlayCountText(row.view_count)}</td>
                <td className={`py-1.5 pr-2 text-right font-mono ${row.like_count == null ? "text-muted" : "text-ink-2"}`}>{skuPlayCountText(row.like_count)}</td>
                <td className="py-1.5 pr-2 font-mono text-[10px]"><MeasuredAt ts={row.measured_at} /></td>
                <td className="py-1.5 pr-2 text-right"><DeltaText value={row.delta?.d7} /></td>
                <td className="py-1.5 pr-2"><ProductLinkChip relation={row.link_relation_type} /></td>
                <td className="py-1.5"><TrackingChip status={row.tracking_status} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GroupRow({
  group,
  expanded,
  focused,
  focusedEvidenceId,
  onToggle,
  setAnchor,
  setEvidenceAnchor,
}: {
  group: SkuPlayGroup;
  expanded: boolean;
  focused: boolean;
  focusedEvidenceId: number;
  onToggle: () => void;
  setAnchor: (node: HTMLDivElement | null) => void;
  setEvidenceAnchor: (evidenceId: number, node: HTMLTableRowElement | null) => void;
}) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const items = Array.isArray(group.items) ? group.items : [];
  return (
    <div
      ref={setAnchor}
      data-vkpi-sku-play-sku={group.sku_code}
      data-vkpi-sku-play-focus={focused ? "active" : "idle"}
      className={`rounded-[10px] border ${expanded ? "border-accent" : "border-line"} bg-card transition-shadow duration-300 ${focused ? "ring-2 ring-accent ring-offset-2 ring-offset-panel" : ""}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-left transition-colors hover:text-ink"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <Chevron size={13} className="flex-none text-muted" />
          <span className="min-w-0 truncate text-[12px] font-semibold text-ink" title={group.sku_name || group.sku_code}>
            {group.sku_name || group.sku_code}
          </span>
          <span className="flex-none font-mono text-[9.5px] text-muted">{group.sku_code}</span>
        </span>
        <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-2">
          <span className="whitespace-nowrap text-muted">
            {num(group.videos) ?? items.length} 视频 · {num(group.kols) ?? "—"} 红人
          </span>
          <span className="whitespace-nowrap">
            累计播放 <b className={`font-mono ${group.total_views == null ? "text-muted" : "text-ink"}`}>{skuPlayCountText(group.total_views)}</b>
          </span>
          <span className="whitespace-nowrap">
            Δ7 天 <DeltaText value={group.delta?.d7} />
          </span>
          <span className="whitespace-nowrap text-[10px]">
            最后实测 <MeasuredAt ts={group.latest_measured_at} />
          </span>
        </span>
      </button>
      {expanded ? (
        <div className="border-t border-line px-3 py-2">
          {items.length === 0 ? (
            <EmptyLine text="本单品暂无被追踪视频行。" />
          ) : (
            <GroupItemsTable
              items={items}
              focusedEvidenceId={focusedEvidenceId}
              setEvidenceAnchor={setEvidenceAnchor}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}

export function SkuPlayModule({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  const [data, setData] = React.useState<VkpiMyKolSkuPlayOverviewResponse | null>(null);
  const [error, setError] = React.useState("");
  const [unsupported, setUnsupported] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [tick, setTick] = React.useState(0);
  const [loadedTick, setLoadedTick] = React.useState(-1);
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set());
  const [highlighted, setHighlighted] = React.useState(false);
  const [pendingTarget, setPendingTarget] = React.useState<(SkuPlayChangedDetail & { refreshTick: number }) | null>(null);
  const anchorRef = React.useRef<HTMLDivElement | null>(null);
  const groupRefs = React.useRef(new Map<string, HTMLDivElement>());
  const evidenceRefs = React.useRef(new Map<number, HTMLTableRowElement>());
  const highlightTimer = React.useRef<number | null>(null);
  const positionFrame = React.useRef<number | null>(null);
  const tickRef = React.useRef(0);

  const nextTick = React.useCallback(() => {
    tickRef.current += 1;
    setTick(tickRef.current);
    return tickRef.current;
  }, []);

  const refreshPreservingTarget = React.useCallback(() => {
    const refreshTick = nextTick();
    setPendingTarget((current) => current ? { ...current, refreshTick } : current);
  }, [nextTick]);

  // 写模型与本纯读模块独立：数据关注成功后立即重读，
  // 避免用户已明确关联 SKU，卡面仍停在旧的“0 / 空态”。
  React.useEffect(() => {
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<SkuPlayChangedDetail>).detail;
      const evidenceId = Number(detail?.evidenceId) || 0;
      const skus = [...new Set((Array.isArray(detail?.skus) ? detail.skus : [])
        .map((sku) => String(sku || "").trim())
        .filter(Boolean))];
      const refreshTick = nextTick();
      setPendingTarget({ evidenceId, skus, refreshTick });
      setHighlighted(true);
      if (positionFrame.current != null) {
        window.cancelAnimationFrame(positionFrame.current);
        positionFrame.current = null;
      }
      if (highlightTimer.current != null) window.clearTimeout(highlightTimer.current);
      // 网络异常时也不会永久高亮；正常响应后会改为目标行定位后的短高亮。
      highlightTimer.current = window.setTimeout(() => {
        setHighlighted(false);
        setPendingTarget(null);
      }, 10_000);
    };
    window.addEventListener(SKU_PLAY_CHANGED_EVENT, refresh);
    return () => {
      window.removeEventListener(SKU_PLAY_CHANGED_EVENT, refresh);
      if (highlightTimer.current != null) window.clearTimeout(highlightTimer.current);
      if (positionFrame.current != null) window.cancelAnimationFrame(positionFrame.current);
    };
  }, [nextTick]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    fetchSkuPlayOverview(apiToken)
      .then((res) => {
        if (!alive) return;
        setUnsupported(false);
        setData(res && typeof res === "object" ? res : null);
        setLoadedTick(tick);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        if (isNotFound(err)) {
          setUnsupported(true);
          return;
        }
        setError(errDetail(err, "读取失败"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, tick]);

  React.useEffect(() => {
    if (!pendingTarget || loadedTick !== pendingTarget.refreshTick) return;
    const groups = Array.isArray(data?.groups) ? data.groups : [];
    const wantedSkus = new Set(pendingTarget.skus.map((sku) => sku.toUpperCase()));
    const matched = groups.filter((group) => (
      wantedSkus.has(String(group.sku_code || "").trim().toUpperCase())
      || (Array.isArray(group.items) && group.items.some((item) => Number(item.evidence_id) === pendingTarget.evidenceId))
    ));
    const matchedCodes = matched.map((group) => group.sku_code);
    if (matchedCodes.length) {
      setExpanded((current) => new Set([...current, ...matchedCodes]));
    }
    if (positionFrame.current != null) window.cancelAnimationFrame(positionFrame.current);
    const frame = window.requestAnimationFrame(() => {
      if (positionFrame.current === frame) positionFrame.current = null;
      // 后续事件或手动刷新已推进 tick 时，旧定位回调绝不再抢焦点。
      if (tickRef.current !== pendingTarget.refreshTick) return;
      const evidenceTarget = pendingTarget.evidenceId > 0
        ? evidenceRefs.current.get(pendingTarget.evidenceId)
        : null;
      const target = evidenceTarget
        || (matchedCodes.length ? groupRefs.current.get(matchedCodes[0]) : null)
        || anchorRef.current;
      target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
      if (target instanceof HTMLTableRowElement) target.focus({ preventScroll: true });
      else target?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
    });
    positionFrame.current = frame;
    if (highlightTimer.current != null) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => {
      setHighlighted(false);
      setPendingTarget(null);
    }, 2400);
    return () => {
      if (positionFrame.current === frame) {
        window.cancelAnimationFrame(frame);
        positionFrame.current = null;
      }
    };
  }, [data, loadedTick, pendingTarget]);

  const wrap = (body: React.ReactNode) => (
    <div
      ref={anchorRef}
      data-vkpi-sku-play-module=""
      data-vkpi-sku-play-highlight={highlighted ? "active" : "idle"}
      data-vkpi-sku-play-evidence-id={pendingTarget?.evidenceId || undefined}
      className={`rounded-[10px] transition-shadow duration-300 ${highlighted ? "ring-2 ring-accent ring-offset-2 ring-offset-panel" : ""}`}
    >
      {body}
    </div>
  );

  if (!apiToken) return wrap(noToken);
  if (unsupported) {
    return wrap(
      <PendingCard>
        <b>该版本暂无单品播放总览</b> —— 后端尚未提供按单品聚合的播放端点;接通后本模块自动点亮,不摆假数。
      </PendingCard>
    );
  }
  if (error) return wrap(<ErrorCard title="单品播放数据读取失败" text={error} />);
  if (!data) return wrap(<LoadingLine text={loading ? "单品播放数据读取中…" : "等待读取…"} />);

  const groups = Array.isArray(data.groups) ? data.groups : [];
  const summary = data.summary;

  const toolbar = (
    <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-2">
      {num(summary?.skus) != null && <Stat label="单品" value={num(summary?.skus)} />}
      {num(summary?.videos) != null && <Stat label="视频" value={num(summary?.videos)} />}
      {num(summary?.kols) != null && <Stat label="红人" value={num(summary?.kols)} />}
      {num(summary?.measured_videos) != null && <Stat label="已实测" value={num(summary?.measured_videos)} />}
      <span className="ml-auto flex items-center gap-1.5">
        <button
          type="button"
          onClick={refreshPreservingTarget}
          className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-[9.5px] text-muted transition-colors hover:text-ink"
          title="重新读取(不触发抓取)"
        >
          <RefreshCw size={10} />
          <span>刷新</span>
        </button>
      </span>
    </div>
  );

  if (groups.length === 0) {
    return wrap(
      <div>
        {toolbar}
        <PendingCard>
          <b>还没有登记「数据关注」的视频</b> —— 在内容墙或 KOL 详情的视频卡上点「数据关注」即可开始追踪,之后按单品在这里汇总播放走势。
        </PendingCard>
      </div>
    );
  }

  return wrap(
    <div>
      {toolbar}
      <div className="grid gap-2">
        {groups.map((group) => (
          <GroupRow
            key={group.sku_code}
            group={group}
            expanded={expanded.has(group.sku_code)}
            focused={Boolean(pendingTarget && (
              pendingTarget.skus.some((sku) => sku.toUpperCase() === group.sku_code.toUpperCase())
              || (Array.isArray(group.items) && group.items.some((item) => Number(item.evidence_id) === pendingTarget.evidenceId))
            ))}
            focusedEvidenceId={pendingTarget?.evidenceId || 0}
            onToggle={() => setExpanded((current) => {
              const next = new Set(current);
              if (next.has(group.sku_code)) next.delete(group.sku_code);
              else next.add(group.sku_code);
              return next;
            })}
            setAnchor={(node) => {
              if (node) groupRefs.current.set(group.sku_code, node);
              else groupRefs.current.delete(group.sku_code);
            }}
            setEvidenceAnchor={(evidenceId, node) => {
              if (node && evidenceId > 0) evidenceRefs.current.set(evidenceId, node);
              else if (evidenceId > 0) evidenceRefs.current.delete(evidenceId);
            }}
          />
        ))}
      </div>
      {data.truncated ? (
        <div className="mt-1 text-[10px] text-muted">被追踪视频超过统计上限,只汇总已读取部分(已截断如实标注)。</div>
      ) : null}
      <div className="mt-1 text-[10px] text-muted">播放/点赞 = 抓取时刻实测读数(非平台实时);未实测 ≠ 0;Δ7 天 = 最近实测 − 窗口基线之和,样本不足显「待积累」;时间按浏览器时区。</div>
    </div>
  );
}
