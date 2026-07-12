import React from "react";
import { m } from "framer-motion";
import { Activity, AlertTriangle, ArrowRight, Play } from "lucide-react";
import { freshnessLabel } from "../../lib/timeLocal";
import "./ai-evidence-cards.css";

type EvidenceCardVariant = "compact" | "standard" | "focus";

const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  facebook: "Facebook",
};

function sourceRecord(value: unknown): Record<string, any> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, any>;
  return value ? { name: String(value) } : {};
}

function sourceUrl(source: Record<string, any>) {
  return String(source.url || source.content_url || source.original_url || "").trim();
}

function hasHttpUrl(value: unknown) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

function isExternalSource(source: Record<string, any>) {
  return String(source.content_origin || source.contentOrigin || "").trim().toLowerCase() === "external";
}

function platformKey(source: Record<string, any>) {
  const raw = [source.platform, source.provider, source.name, source.title, sourceUrl(source)]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (raw.includes("youtube") || raw.includes("youtu.be")) return "youtube";
  if (raw.includes("tiktok")) return "tiktok";
  if (raw.includes("instagram")) return "instagram";
  if (raw.includes("facebook") || raw.includes("fb.watch")) return "facebook";
  return "";
}

function platformLabel(source: Record<string, any>) {
  const key = platformKey(source);
  return PLATFORM_LABELS[key] || String(source.platform || "平台未记录");
}

function contentFormat(source: Record<string, any>) {
  const explicit = source.content_format || source.contentFormat || source.format || source.content_type;
  if (explicit) return String(explicit);
  const key = platformKey(source);
  const url = sourceUrl(source).toLowerCase();
  if (key === "youtube" && url.includes("/shorts/")) return "Shorts";
  if (key === "youtube" && (url.includes("watch?") || url.includes("youtu.be/"))) return "视频";
  if (key === "tiktok" && url.includes("/video/")) return "短视频";
  if (key === "instagram" && url.includes("/reel")) return "Reel";
  if (key === "instagram" && url.includes("/p/")) return "帖子";
  if (key === "facebook" && url.includes("/reel")) return "Reel";
  if (key === "facebook" && (url.includes("/video") || url.includes("fb.watch"))) return "视频";
  return "格式未记录";
}

function sourceAuthor(source: Record<string, any>) {
  const author = String(source.author || source.author_name || source.creator_name || source.channel_name || "").trim();
  const handle = String(source.author_handle || source.creator_handle || "").trim().replace(/^@/, "");
  if (author && handle && author.toLowerCase() !== handle.toLowerCase()) return `${author} · @${handle}`;
  return author || (handle ? `@${handle}` : "作者未记录");
}

function sourcePublished(source: Record<string, any>) {
  const raw = String(source.published_at || source.publish_date || source.posted_at || "").trim();
  if (!raw) return "未记录";
  return raw.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || raw;
}

function sourceFreshness(source: Record<string, any>, alert: Record<string, any>) {
  const explicit = String(source.freshnessLabel || source.freshness_label || "").trim();
  if (alert.stale || alert.freshnessStatus === "stale" || source.sourceStatus === "expired" || source.source_status === "expired") {
    return explicit ? `过期 · ${explicit}` : "快照过期";
  }
  if (explicit) return explicit;
  const published = String(source.published_at || source.publish_date || source.posted_at || "").trim();
  if (!published) return "新鲜度未知";
  const parsed = new Date(published);
  if (Number.isNaN(parsed.getTime())) return "新鲜度未知";
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days < -1) return "发布时间待核验";
  if (days <= 0) return "今天发布";
  return `${days} 天前`;
}

function sourceName(source: Record<string, any>) {
  return String(source.title || source.name || source.provider || source.sourceTable || source.source_table || "来源未记录");
}

function spanVariant(span: number): EvidenceCardVariant {
  if (span >= 10) return "focus";
  if (span >= 6) return "standard";
  return "compact";
}

function useEvidenceCardLayout(explicitVariant?: EvidenceCardVariant) {
  const ref = React.useRef<HTMLElement | null>(null);
  const [layout, setLayout] = React.useState<{ variant: EvidenceCardVariant; span: number | null }>({
    variant: explicitVariant || "compact",
    span: null,
  });

  React.useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const moduleElement = element.closest<HTMLElement>(".vkpi-board-module");
    const boardElement = moduleElement?.closest<HTMLElement>(".vkpi-editable-board");
    const update = () => {
      const parsedSpan = Number(moduleElement?.style.getPropertyValue("--vkpi-module-span") || "");
      const span = Number.isFinite(parsedSpan) && parsedSpan > 0 ? parsedSpan : null;
      const width = element.getBoundingClientRect().width;
      const gridTemplate = boardElement ? window.getComputedStyle(boardElement).gridTemplateColumns : "";
      const repeatedColumns = Number(gridTemplate.match(/repeat\(\s*(\d+)/)?.[1] || 0);
      const computedColumns = gridTemplate.split(/\s+/).filter((column) => column && column !== "none");
      const boardColumnCount = repeatedColumns || (computedColumns.length > 1 ? computedColumns.length : null);
      const effectiveSpan = boardColumnCount && boardColumnCount < 12 ? boardColumnCount : span || 4;
      let nextVariant = explicitVariant || spanVariant(effectiveSpan);
      if (!explicitVariant && width > 0) {
        if (width < 520) nextVariant = "compact";
        else if (width < 900) nextVariant = "standard";
        else if (!moduleElement) nextVariant = "focus";
      }
      setLayout((current) => current.variant === nextVariant && current.span === span
        ? current
        : { variant: nextVariant, span });
    };
    update();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    resizeObserver?.observe(element);
    const mutationObserver = moduleElement && typeof MutationObserver !== "undefined" ? new MutationObserver(update) : null;
    mutationObserver?.observe(moduleElement as HTMLElement, { attributes: true, attributeFilter: ["style"] });
    window.addEventListener("resize", update);
    return () => {
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [explicitVariant]);

  return { ref, ...layout };
}

export function SignalsAlertsCard({ alerts, onAlertClick, onViewAll, freshnessAt, variant }: any) {
  const rows = Array.isArray(alerts) ? alerts : [];
  const { ref, variant: layoutVariant, span } = useEvidenceCardLayout(variant);
  const visibleRows = rows.slice(0, layoutVariant === "compact" ? 3 : 4);
  const allStale = rows.length > 0 && rows.every((alert: Record<string, any>) => alert?.stale || alert?.freshnessStatus === "stale");
  const headerFreshness = allStale
    ? "信号快照过期"
    : freshnessAt
      ? freshnessLabel(freshnessAt)
      : "更新时间未记录";

  const sevColor: Record<string, string> = {
    high: "#ef4444",
    medium: "#f59e0b",
    low: "#10b981",
    info: "#06b6d4",
  };
  const sevBg: Record<string, string> = {
    high: "rgba(239, 68, 68, 0.06)",
    medium: "rgba(245, 158, 11, 0.06)",
    low: "rgba(16, 185, 129, 0.06)",
    info: "rgba(6, 182, 212, 0.06)",
  };
  const sevTextColor: Record<string, string> = {
    high: "#fca5a5",
    medium: "#fcd34d",
    low: "#6ee7b7",
    info: "#67e8f9",
  };

  const traceableCount = rows.filter((alert: Record<string, any>) =>
    (Array.isArray(alert?.sources) ? alert.sources : []).some((source: unknown) => hasHttpUrl(sourceUrl(sourceRecord(source))))).length;
  const externalCount = rows.filter((alert: Record<string, any>) =>
    (Array.isArray(alert?.sources) ? alert.sources : []).some((source: unknown) => isExternalSource(sourceRecord(source)))).length;

  return (
    <m.article
      ref={ref as React.Ref<HTMLElement>}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.2 }}
      className={`signals-evidence-card is-${layoutVariant} flex h-full flex-col rounded-xl border border-line p-4`}
      data-layout-variant={layoutVariant}
      data-column-span={span || undefined}
    >
      <header className="mb-3 flex min-w-0 items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <AlertTriangle size={14} className="shrink-0 text-crit" />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink">Signals &amp; Alerts</h3>
            <p className="mt-0.5 text-[9px] text-muted">外部市场信号与原始来源</p>
          </div>
        </div>
        <div className={`flex shrink-0 items-center gap-1.5 text-[9px] ${allStale || !freshnessAt ? "text-warn" : "text-muted"}`}>
          <Activity size={10} />
          <span>{headerFreshness}</span>
        </div>
      </header>

      <div className="signals-evidence-card__list flex-1">
        {!visibleRows.length ? (
          <div className="rounded-md border border-dashed border-line px-3 py-8 text-center text-[11px] text-muted" role="status">
            暂无真实市场信号，来源状态不可判定
          </div>
        ) : visibleRows.map((alert: Record<string, any>) => {
          const sources = (Array.isArray(alert.sources) ? alert.sources : []).map(sourceRecord);
          const featuredSource = sources.find((source) => isExternalSource(source)) || sources.find((source) => hasHttpUrl(sourceUrl(source))) || sources[0] || {};
          const traceableSourceCount = sources.filter((source) => hasHttpUrl(sourceUrl(source))).length;
          const sourceHasLink = traceableSourceCount > 0;
          const featuredSourceHasLink = hasHttpUrl(sourceUrl(featuredSource));
          const sourcePlatform = platformKey(featuredSource);
          const externalPlatform = isExternalSource(featuredSource) ? sourcePlatform : "";
          const stale = Boolean(alert.stale || alert.freshnessStatus === "stale");
          const severity = sevColor[alert.severity] ? alert.severity : "info";
          const thumbnail = String(featuredSource.thumbnail_url || featuredSource.thumbnail || "");
          const mentions = Number(alert.totalMentions) || 0;
          return (
            <div
              key={alert.id}
              role="button"
              tabIndex={0}
              onClick={() => onAlertClick?.(alert)}
              onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === " ") && event.target === event.currentTarget) {
                  event.preventDefault();
                  onAlertClick?.(alert);
                }
              }}
              className="group min-w-0 cursor-pointer rounded-md px-2.5 py-2 transition-colors hover:bg-accent-soft focus:outline-none focus:ring-1 focus:ring-accent"
              style={{ background: sevBg[severity], borderLeft: `2px solid ${sevColor[severity]}` }}
            >
              <div className="mb-1 flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-[11px] font-medium" style={{ color: sevTextColor[severity] }}>{alert.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {externalPlatform ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-px text-[8px] text-accent">外部市场样例</span> : null}
                    {stale ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-1.5 py-px text-[8px] text-warn">过期快照</span> : null}
                    {!featuredSourceHasLink ? <span className="rounded border border-line px-1.5 py-px text-[8px] text-warn">样例无原始链接</span> : null}
                  </div>
                </div>
                <span className="shrink-0 text-[9px] text-muted">{alert.time || "时间未记录"}</span>
              </div>

              <div className="mb-1.5 line-clamp-2 text-[10px] leading-relaxed text-muted">{alert.desc || alert.summary || "暂无摘要"}</div>

              {sourcePlatform ? (
                <div className="mb-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[8.5px] text-muted">
                  <span>平台 {platformLabel(featuredSource)}</span>
                  <span>格式 {contentFormat(featuredSource)}</span>
                  <span>作者 {sourceAuthor(featuredSource)}</span>
                  <span>发布 {sourcePublished(featuredSource)}</span>
                  <span className={stale ? "text-warn" : ""}>新鲜度 {sourceFreshness(featuredSource, alert)}</span>
                  <span>来源 {sourceName(featuredSource)}</span>
                </div>
              ) : (
                <div className="mb-1.5 text-[8.5px] text-muted">
                  来源 {sourceName(featuredSource)} · {sourceHasLink ? "原始链接可回跳" : "原始链接未保留"}
                </div>
              )}

              {thumbnail ? (
                <div className="mb-1.5 flex items-center gap-2 rounded border border-line bg-card p-1.5 text-[8.5px] text-muted">
                  <span className="relative h-8 w-12 shrink-0 overflow-hidden rounded bg-[var(--ds-bg-2)]">
                    <img src={thumbnail} alt="" className="h-full w-full object-cover" />
                    <span className="absolute inset-0 flex items-center justify-center bg-black/15 text-white"><Play size={9} /></span>
                  </span>
                  <span>{featuredSourceHasLink ? "来源缩略图 · 播放与原帖入口见详情" : "来源缩略图 · 原帖链接未保留"}</span>
                </div>
              ) : null}

              {Array.isArray(alert.brands) && alert.brands.length ? (
                <div className="mb-1 flex flex-wrap items-center gap-1">
                  {alert.brands.slice(0, 4).map((brand: string) => (
                    <button
                      key={brand}
                      type="button"
                      title={`查看 ${brand} 相关信号`}
                      onClick={(event) => {
                        event.stopPropagation();
                        onAlertClick?.({ ...alert, brand });
                      }}
                      className="rounded border border-line bg-card px-1.5 py-0.5 text-[9px] font-semibold text-ink-2 transition-colors hover:bg-accent-soft"
                    >
                      {brand}
                    </button>
                  ))}
                </div>
              ) : null}

              <div className="flex min-w-0 items-center justify-between gap-2 text-[9px]">
                <span className="min-w-0 text-muted" title={alert.sourceDetail || sources.map(sourceName).filter(Boolean).join(" · ")}>
                  {alert.sourceLine || (sourceHasLink ? `${sources.length} 条来源记录` : "来源不可回溯")}
                </span>
                <span className="flex shrink-0 items-center gap-1.5">
                  {mentions ? <span className="rounded bg-card px-1 py-0.5 text-muted">证据记录 {mentions}</span> : null}
                  <span className={`rounded bg-card px-1 py-0.5 ${traceableSourceCount ? "text-muted" : "text-warn"}`}>{traceableSourceCount ? `可回源 ${traceableSourceCount}` : "无可回源"}</span>
                  <span className={stale ? "font-semibold text-warn" : "font-semibold text-good"}>{stale ? "历史" : (alert.trendPct || "待核验")}</span>
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <footer className="mt-3 flex min-w-0 items-center justify-between gap-3 border-t border-line pt-2">
        <div className={`min-w-0 text-[9px] ${rows.length && traceableCount === 0 ? "text-warn" : "text-muted"}`}>
          {rows.length ? `${traceableCount}/${rows.length} 条信号可回源${externalCount ? ` · ${externalCount} 条外部平台样例` : ""}` : "无信号来源"}
        </div>
        <button type="button" onClick={onViewAll} className="inline-flex shrink-0 items-center gap-1 text-[10px] text-ink-2 hover:text-ink">
          查看全部 <ArrowRight size={10} />
        </button>
      </footer>
    </m.article>
  );
}
