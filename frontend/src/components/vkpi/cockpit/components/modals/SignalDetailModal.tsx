import React from "react";
import { m } from "framer-motion";
import {
  AlertTriangle,
  Copy,
  ExternalLink,
  Play,
  X,
} from "lucide-react";
import { useT } from "../../lib/i18n";
import "../ai-evidence-cards.css";

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
  return String(source.url || source.content_url || source.original_url || source.originalUrl || "").trim();
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

function authorLabel(source: Record<string, any>) {
  const author = String(source.author || source.author_name || source.creator_name || source.channel_name || "").trim();
  const handle = String(source.author_handle || source.creator_handle || "").trim().replace(/^@/, "");
  if (author && handle && author.toLowerCase() !== handle.toLowerCase()) return `${author} · @${handle}`;
  if (author) return author;
  if (handle) return `@${handle}`;
  return "作者未记录";
}

function publishedValue(source: Record<string, any>) {
  return String(source.published_at || source.publish_date || source.posted_at || "").trim();
}

function dateLabel(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "未记录";
  return raw.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || raw;
}

function sourceFreshness(source: Record<string, any>, stale: boolean) {
  const explicit = String(source.freshnessLabel || source.freshness_label || "").trim();
  const expired = stale || source.sourceStatus === "expired" || source.source_status === "expired";
  if (explicit) return expired ? `过期 · ${explicit}` : explicit;
  const published = publishedValue(source);
  if (!published) return expired ? "快照过期 · 内容新鲜度未知" : "新鲜度未知";
  const parsed = new Date(published);
  if (Number.isNaN(parsed.getTime())) return expired ? "快照过期 · 内容新鲜度未知" : "新鲜度未知";
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  const age = days < -1 ? "发布时间待核验" : days <= 0 ? "今天发布" : `${days} 天前`;
  return expired ? `快照过期 · ${age}` : age;
}

function sourceName(source: Record<string, any>) {
  return String(source.title || source.name || source.provider || source.sourceTable || source.source_table || "未命名来源");
}

function sourceRecordId(source: Record<string, any>) {
  const table = source.sourceTable || source.source_table || source.ledger_table || source.provider || "";
  const id = source.sourceId ?? source.source_id ?? source.ledger_id;
  if (table && id != null && id !== "") return `${table}:${id}`;
  if (table) return String(table);
  return source.value ? String(source.value) : "来源记录 ID 未保留";
}

function relationType(source: Record<string, any>) {
  return String(source.relationType || source.relation_type || "");
}

function SignalSourceCard({ source, stale }: { source: Record<string, any>; stale: boolean }) {
  const [playbackFailed, setPlaybackFailed] = React.useState(false);
  const [thumbnailFailed, setThumbnailFailed] = React.useState(false);
  const url = sourceUrl(source);
  const hasOriginal = hasHttpUrl(url);
  const playbackUrl = String(source.playback_url || source.playbackUrl || "").trim();
  const thumbnailUrl = String(source.thumbnail_url || source.thumbnail || source.image_url || "").trim();
  const hasMedia = (playbackUrl && !playbackFailed) || (thumbnailUrl && !thumbnailFailed);
  const platform = platformKey(source);
  const relation = relationType(source);
  const title = sourceName(source);
  const sourceExpired = source.sourceStatus === "expired" || source.source_status === "expired";

  React.useEffect(() => {
    setPlaybackFailed(false);
    setThumbnailFailed(false);
  }, [playbackUrl, thumbnailUrl]);

  const thumbnail = (
    <>
      <img src={thumbnailUrl} alt={title} className="h-full min-h-[112px] w-full object-cover" onError={() => setThumbnailFailed(true)} />
      {platform ? (
        <span className="absolute inset-0 flex items-center justify-center bg-black/15">
          <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/30 bg-black/60 text-white"><Play size={12} fill="currentColor" /></span>
        </span>
      ) : null}
    </>
  );

  return (
    <article className={`signal-evidence-modal__source ${hasMedia ? "has-media" : ""} overflow-hidden rounded-lg border border-line bg-panel`}>
      {hasMedia ? (
        <div className="relative min-h-[112px] overflow-hidden border-b border-line bg-[var(--ds-bg-2)] sm:border-b-0 sm:border-r">
          {playbackUrl && !playbackFailed ? (
            <video controls preload="metadata" poster={thumbnailUrl || undefined} className="h-full min-h-[112px] w-full object-cover" onError={() => setPlaybackFailed(true)} aria-label={`${title} 缓存播放`}>
              <source src={playbackUrl} />
            </video>
          ) : hasOriginal ? (
            <a href={url} target="_blank" rel="noreferrer" className="relative block h-full min-h-[112px]" aria-label={`打开 ${title} 原帖`}>
              {thumbnail}
            </a>
          ) : <div className="relative h-full min-h-[112px]">{thumbnail}</div>}
          <span className="absolute bottom-1.5 left-1.5 rounded border border-white/15 bg-black/65 px-1.5 py-0.5 text-[8px] text-white">
            {playbackUrl && !playbackFailed ? "缓存播放" : "来源缩略图"}
          </span>
        </div>
      ) : null}

      <div className="min-w-0 p-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-1.5">
              {platform && isExternalSource(source) ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-0.5 text-[8.5px] text-accent">外部市场样例</span> : null}
              {relation === "grounding" ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-good)_30%,transparent)] bg-good-soft px-1.5 py-0.5 text-[8.5px] text-good">直接来源</span> : null}
              {relation === "brand_context" ? <span className="rounded border border-line px-1.5 py-0.5 text-[8.5px] text-muted">关联样例 · 非直接证明</span> : null}
              {stale || sourceExpired ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-1.5 py-0.5 text-[8.5px] text-warn">历史</span> : null}
            </div>
            <h4 className="text-[11px] font-medium leading-relaxed text-ink-2">{title}</h4>
          </div>
          {hasOriginal ? (
            <a href={url} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line bg-card px-2 py-1 text-[9px] text-ink-2 hover:text-ink">
              原帖 <ExternalLink size={9} />
            </a>
          ) : <span className="shrink-0 rounded border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] px-1.5 py-1 text-[8px] text-warn">无原始链接</span>}
        </div>

        {platform ? (
          <dl className="evidence-detail-grid mt-2.5">
            <div><dt>平台</dt><dd>{platformLabel(source)}</dd></div>
            <div><dt>内容格式</dt><dd>{contentFormat(source)}</dd></div>
            <div><dt>作者</dt><dd>{authorLabel(source)}</dd></div>
            <div><dt>发布</dt><dd>{dateLabel(publishedValue(source))}</dd></div>
            <div><dt>新鲜度</dt><dd className={stale || sourceExpired ? "text-warn" : ""}>{sourceFreshness(source, stale)}</dd></div>
            <div><dt>来源</dt><dd>{sourceRecordId(source)}</dd></div>
          </dl>
        ) : (
          <div className="mt-2 space-y-1 text-[9px] text-muted">
            <div>来源 {sourceRecordId(source)}</div>
            <div>新鲜度 {sourceFreshness(source, stale)}</div>
            {source.observedAt || source.observed_at ? <div>采集 {dateLabel(source.observedAt || source.observed_at)}</div> : null}
          </div>
        )}
      </div>
    </article>
  );
}

export function SignalDetailModal({ alert, onClose }: any) {
  const { t } = useT();
  if (!alert) return null;

  const sources: Record<string, any>[] = (Array.isArray(alert.sources) ? alert.sources : []).map(sourceRecord);
  const traceableSources = sources.filter((source: Record<string, any>) => hasHttpUrl(sourceUrl(source)));
  const sourceMentions = sources.filter((source: Record<string, any>) => Number(source.mentions) > 0);
  const externalSources = sources.filter((source: Record<string, any>) => isExternalSource(source));
  const impactRows = Array.isArray(alert.impact) && alert.impact.length
    ? alert.impact
    : alert.raw?.impact
      ? [{ level: "影响", text: String(alert.raw.impact) }]
      : [];
  const actionRows = Array.isArray(alert.actions) ? alert.actions : [];
  const summaryText = String(alert.summary || alert.desc || "");
  const stale = Boolean(alert.stale || alert.freshnessStatus === "stale");
  const contextOnlySources = alert.sourceRelation === "brand_context" || (sources.length > 0 && sources.every((source) => relationType(source) === "brand_context"));
  const thumbnails: string[] = (Array.isArray(alert.thumbnails) ? alert.thumbnails : []).map(String).filter(Boolean);
  const severityColors: Record<string, string> = {
    high: "#ef4444",
    medium: "#f59e0b",
    low: "#10b981",
    info: "#06b6d4",
  };
  const severityColor = severityColors[alert.severity] || severityColors.info;

  return (
    <m.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="signal-evidence-modal cockpit-modal fixed inset-0 z-[9999] flex items-center justify-center overflow-y-auto bg-black/65 p-3 backdrop-blur-lg md:p-4"
      onClick={onClose}
    >
      <m.div
        initial={{ scale: 0.95, opacity: 0, y: 20 }}
        animate={{ scale: 1, opacity: 1, y: 0 }}
        exit={{ scale: 0.95, opacity: 0 }}
        onClick={(event: React.MouseEvent) => event.stopPropagation()}
        className="relative flex max-h-[94vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-line bg-card shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="signal-detail-title"
      >
        <header className="shrink-0 border-b border-line px-4 py-3.5 md:px-5">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg" style={{ background: `${severityColor}22`, color: severityColor }}>
              <AlertTriangle size={18} />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id="signal-detail-title" className="text-base font-semibold leading-snug text-ink">{alert.title}</h2>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[9.5px] text-muted">
                <span>检测 {alert.time || "时间未记录"}</span>
                <span>原始链接 {traceableSources.length}/{sources.length}</span>
                <span className={stale ? "font-semibold text-warn" : "font-semibold text-good"}>{stale ? "过期快照" : (alert.trendPct || "趋势未记录")}</span>
                {externalSources.length ? <span className="text-accent">外部平台样例 {externalSources.length}</span> : null}
              </div>
            </div>
            <button type="button" onClick={onClose} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-panel text-muted hover:bg-accent-soft hover:text-ink" aria-label="关闭" title="关闭">
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-4 md:p-5">
          {stale ? (
            <div className="flex items-start gap-2 rounded-lg border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-3 py-2.5 text-[11px] text-warn">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>该信号快照已过期，只能用于历史复盘；执行前需重新生成并复核原始来源。</span>
            </div>
          ) : null}

          {summaryText ? (
            <section>
              <h3 className="mb-2 text-[10px] font-medium uppercase text-muted">主要内容</h3>
              <p className="rounded-lg border border-line bg-panel px-3 py-3 text-[12px] leading-relaxed text-ink-2">{summaryText}</p>
            </section>
          ) : null}

          <section>
            <div className="mb-2 flex items-end justify-between gap-3">
              <div>
                <h3 className="text-[10px] font-medium uppercase text-muted">来源与外部市场样例</h3>
                <p className="mt-0.5 text-[9px] text-muted">平台、格式、作者、发布时间、新鲜度与来源记录分开显示</p>
              </div>
              <span className="shrink-0 font-mono text-[9px] text-muted">{traceableSources.length}/{sources.length} traceable</span>
            </div>
            {contextOnlySources ? (
              <div className="mb-2 rounded-md border border-[color:color-mix(in_srgb,var(--ds-warn)_25%,transparent)] bg-warn-soft px-2.5 py-2 text-[10px] leading-relaxed text-warn">
                该历史快照未保留生成时的原始引文。下列是同品牌的关联市场样例，不能作为本条信号的直接证明。
              </div>
            ) : null}
            {sources.length > 0 && traceableSources.length === 0 ? (
              <div className="mb-2 rounded-md border border-[color:color-mix(in_srgb,var(--ds-warn)_25%,transparent)] bg-warn-soft px-2.5 py-2 text-[10px] text-warn">
                仅保留了来源标签或记录 ID，未保留任何原始链接，当前无法回跳核验。
              </div>
            ) : null}
            {sources.length ? (
              <div className="space-y-2">
                {sources.map((source, index) => <SignalSourceCard key={`${sourceUrl(source) || sourceName(source)}-${index}`} source={source} stale={stale} />)}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-line py-8 text-center text-[11px] text-warn" role="status">
                未保留来源记录，平台、作者、发布时间与原帖均无法核验
              </div>
            )}
          </section>

          {thumbnails.length ? (
            <section>
              <h3 className="mb-2 text-[10px] font-medium uppercase text-muted">未绑定来源的关联预览</h3>
              <p className="mb-2 text-[9px] text-warn">后端未提供逐图来源 ID；这些图片不作为可回溯证据。</p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
                {thumbnails.map((src, index) => (
                  <div key={`${src}-${index}`} className="aspect-video overflow-hidden rounded-md border border-line bg-panel">
                    <img src={src} alt={`关联预览 ${index + 1}`} className="h-full w-full object-cover" />
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {sourceMentions.length ? (
            <section>
              <h3 className="mb-2 text-[10px] font-medium uppercase text-muted">来源提及分布</h3>
              <div className="space-y-1.5">
                {sourceMentions.map((source, index) => {
                  const maxMentions = Math.max(1, ...sourceMentions.map((item) => Number(item.mentions) || 0));
                  const mentions = Number(source.mentions) || 0;
                  return (
                    <div key={`${sourceName(source)}-${index}`} className="flex items-center gap-2 text-[11px]">
                      <span className="w-32 shrink-0 text-muted">{sourceName(source)}</span>
                      <span className="h-2 flex-1 overflow-hidden rounded-full bg-panel">
                        <span className="block h-full rounded-full" style={{ width: `${(mentions / maxMentions) * 100}%`, background: severityColor }} />
                      </span>
                      <span className="w-12 text-right font-semibold text-ink-2">{mentions}</span>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}

          {impactRows.length ? (
            <section>
              <h3 className="mb-2 text-[10px] font-medium uppercase text-muted">对 Viltrox 的影响</h3>
              <div className="space-y-1.5">
                {impactRows.map((impact: any, index: number) => (
                  <div key={`${impact.text}-${index}`} className="flex items-start gap-2 text-[11px] text-ink-2">
                    <span className="shrink-0 font-medium text-warn">{impact.level || "影响"}</span>
                    <span>{impact.text}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {actionRows.length ? (
            <section>
              <h3 className="mb-2 text-[10px] font-medium uppercase text-muted">建议行动</h3>
              <div className="space-y-1.5">
                {actionRows.map((action: any, index: number) => (
                  <div key={`${action}-${index}`} className="flex items-start gap-2 rounded-md border border-line bg-panel px-3 py-2 text-[11px] text-ink-2">
                    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[9px] font-bold text-accent">{index + 1}</span>
                    <span className="min-w-0 flex-1">{String(action)}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-2.5 md:px-5">
          <div className={`text-[10px] ${stale || traceableSources.length === 0 ? "text-warn" : "text-muted"}`}>
            {stale ? "历史快照" : "当前快照"} · {traceableSources.length ? `${traceableSources.length} 条来源可回跳` : "无可回跳来源"}
          </div>
          <div className="flex gap-1.5">
            <button type="button" onClick={onClose} className="rounded-md border border-line bg-panel px-3 py-1 text-[11px] text-ink-2 hover:bg-accent-soft">{t("关闭")}</button>
            <button
              type="button"
              onClick={async () => {
                const text = [
                  alert.title,
                  summaryText ? `\n${summaryText}` : "",
                  actionRows.length ? `\n建议行动:\n${actionRows.map((action: any, index: number) => `${index + 1}. ${String(action)}`).join("\n")}` : "",
                ].filter(Boolean).join("\n");
                try { await navigator.clipboard.writeText(text); } catch { /* Clipboard may be unavailable in read-only contexts. */ }
              }}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[11px] font-medium text-white hover:opacity-90"
              title="复制信号与建议行动"
            >
              <Copy size={10} /> 复制建议
            </button>
          </div>
        </footer>
      </m.div>
    </m.div>
  );
}
