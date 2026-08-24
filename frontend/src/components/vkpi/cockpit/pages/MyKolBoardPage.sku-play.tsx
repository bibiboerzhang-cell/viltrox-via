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

function GroupItemsTable({ items }: { items: SkuPlayItem[] }) {
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
            <th className="py-1.5 font-semibold">状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row) => {
            const title = row.title || row.content_url || `#${row.evidence_id}`;
            return (
              <tr key={row.evidence_id} className="border-t border-line">
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
                <td className="py-1.5"><TrackingChip status={row.tracking_status} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GroupRow({ group, expanded, onToggle }: { group: SkuPlayGroup; expanded: boolean; onToggle: () => void }) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const items = Array.isArray(group.items) ? group.items : [];
  return (
    <div className={`rounded-[10px] border ${expanded ? "border-accent" : "border-line"} bg-card`}>
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
            <GroupItemsTable items={items} />
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
  const [expanded, setExpanded] = React.useState("");

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

  if (!apiToken) return <>{noToken}</>;
  if (unsupported) {
    return (
      <PendingCard>
        <b>该版本暂无单品播放总览</b> —— 后端尚未提供按单品聚合的播放端点;接通后本模块自动点亮,不摆假数。
      </PendingCard>
    );
  }
  if (error) return <ErrorCard title="单品播放数据读取失败" text={error} />;
  if (!data) return <LoadingLine text={loading ? "单品播放数据读取中…" : "等待读取…"} />;

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
          onClick={() => setTick((t) => t + 1)}
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
    return (
      <div>
        {toolbar}
        <PendingCard>
          <b>还没有登记「数据关注」的视频</b> —— 在内容墙或 KOL 详情的视频卡上点「数据关注」即可开始追踪,之后按单品在这里汇总播放走势。
        </PendingCard>
      </div>
    );
  }

  return (
    <div>
      {toolbar}
      <div className="grid gap-2">
        {groups.map((group) => (
          <GroupRow
            key={group.sku_code}
            group={group}
            expanded={expanded === group.sku_code}
            onToggle={() => setExpanded((current) => (current === group.sku_code ? "" : group.sku_code))}
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
