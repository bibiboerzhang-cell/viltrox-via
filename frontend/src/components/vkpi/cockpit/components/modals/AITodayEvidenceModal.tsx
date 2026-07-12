import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowUpRight,
  Database,
  ExternalLink,
  Film,
  Play,
  Sparkles,
  X,
} from "lucide-react";
import "../ai-evidence-cards.css";

const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  facebook: "Facebook",
};

function metric(value: unknown) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return "—";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(numberValue);
}

function hasHttpUrl(value: unknown) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

function isExternalEvidence(item: Record<string, any>) {
  return String(item.content_origin || item.contentOrigin || "").trim().toLowerCase() === "external";
}

function itemUrl(item: Record<string, any>) {
  return String(item.content_url || item.url || item.original_url || "").trim();
}

function platformKey(item: Record<string, any>) {
  const raw = [item.platform, item.provider, item.source, item.title, itemUrl(item)]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (raw.includes("youtube") || raw.includes("youtu.be")) return "youtube";
  if (raw.includes("tiktok")) return "tiktok";
  if (raw.includes("instagram")) return "instagram";
  if (raw.includes("facebook") || raw.includes("fb.watch")) return "facebook";
  return "";
}

function platformLabel(item: Record<string, any>) {
  const key = platformKey(item);
  return PLATFORM_LABELS[key] || String(item.platform || "平台未记录");
}

function contentFormat(item: Record<string, any>) {
  const explicit = item.content_format || item.contentFormat || item.format || item.content_type;
  if (explicit) return String(explicit);
  const key = platformKey(item);
  const url = itemUrl(item).toLowerCase();
  if (key === "youtube" && url.includes("/shorts/")) return "Shorts";
  if (key === "youtube" && (url.includes("watch?") || url.includes("youtu.be/"))) return "视频";
  if (key === "tiktok" && url.includes("/video/")) return "短视频";
  if (key === "instagram" && url.includes("/reel")) return "Reel";
  if (key === "instagram" && url.includes("/p/")) return "帖子";
  if (key === "facebook" && url.includes("/reel")) return "Reel";
  if (key === "facebook" && (url.includes("/video") || url.includes("fb.watch"))) return "视频";
  return "格式未记录";
}

function authorLabel(item: Record<string, any>) {
  const name = String(item.creator_name || item.author_name || item.author || item.channel_name || "").trim();
  const handle = String(item.creator_handle || item.author_handle || "").trim().replace(/^@/, "");
  if (name && handle && name.toLowerCase() !== handle.toLowerCase()) return `${name} · @${handle}`;
  if (name) return name;
  if (handle) return `@${handle}`;
  return "作者未记录";
}

function publishedValue(item: Record<string, any>) {
  return String(item.published_at || item.publish_date || item.posted_at || "").trim();
}

function dateLabel(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "未记录";
  return raw.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || raw;
}

function ageLabel(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "新鲜度未知";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "新鲜度未知";
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days < -1) return "发布时间待核验";
  if (days <= 0) return "今天发布";
  if (days === 1) return "1 天前";
  return `${days} 天前`;
}

function itemFreshness(item: Record<string, any>, snapshotStale: boolean) {
  const own = String(item.freshness_label || item.freshnessLabel || "").trim() || ageLabel(publishedValue(item));
  const itemExpired = item.source_status === "expired" || item.sourceStatus === "expired";
  return snapshotStale || itemExpired ? `过期上下文 · ${own}` : own;
}

function sourceRecordLabel(item: Record<string, any>) {
  const table = item.ledger_table || item.source_table || item.sourceTable || item.provider || "";
  const id = item.ledger_id ?? item.source_id ?? item.sourceId;
  if (table && id != null && id !== "") return `${table}:${id}`;
  if (table) return String(table);
  const url = itemUrl(item);
  if (hasHttpUrl(url)) return `${platformLabel(item)} 原帖`;
  return "来源未记录";
}

function MetadataGrid({ item, stale, source }: { item: Record<string, any>; stale: boolean; source: string }) {
  return (
    <dl className="evidence-detail-grid is-wide mt-3 rounded-lg border border-line bg-card px-3 py-2.5">
      <div><dt>平台</dt><dd>{platformLabel(item)}</dd></div>
      <div><dt>内容格式</dt><dd>{contentFormat(item)}</dd></div>
      <div><dt>作者</dt><dd>{authorLabel(item)}</dd></div>
      <div><dt>发布</dt><dd>{dateLabel(publishedValue(item))}</dd></div>
      <div><dt>新鲜度</dt><dd className={stale ? "text-warn" : ""}>{itemFreshness(item, stale)}</dd></div>
      <div><dt>来源</dt><dd>{source}</dd></div>
    </dl>
  );
}

function VideoEvidenceCard({ video, snapshotStale }: { video: Record<string, any>; snapshotStale: boolean }) {
  const [videoFailed, setVideoFailed] = useState(false);
  const [thumbnailFailed, setThumbnailFailed] = useState(false);
  const playbackUrl = String(video.playback_url || "").trim();
  const thumbnailUrl = String(video.thumbnail_url || "").trim();
  const playbackSource = String(video.playback_source || "").trim();
  const originalUrl = itemUrl(video);
  const hasOriginal = hasHttpUrl(originalUrl);
  const sourceRefs = Array.isArray(video.source_refs) ? video.source_refs : [];
  const sourceSummary = sourceRefs.length
    ? `${sourceRefs.length} 条证据记录`
    : hasOriginal
      ? `${platformLabel(video)} 原帖 · 证据表未记录`
      : "来源未记录";
  const title = String(video.title || "未命名外部样例");

  useEffect(() => {
    setVideoFailed(false);
    setThumbnailFailed(false);
  }, [playbackUrl, thumbnailUrl]);

  const thumbnail = (
    <>
      <img
        src={thumbnailUrl}
        alt={title}
        className="h-full min-h-[170px] w-full object-cover transition duration-300 group-hover:scale-[1.02] motion-reduce:transition-none"
        onError={() => setThumbnailFailed(true)}
      />
      <span className="absolute inset-0 flex items-center justify-center bg-black/15">
        <span className="flex h-10 w-10 items-center justify-center rounded-full border border-white/30 bg-black/60 text-white backdrop-blur">
          <Play size={16} fill="currentColor" />
        </span>
      </span>
    </>
  );

  return (
    <article className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="grid min-h-[190px] grid-cols-1 md:grid-cols-[236px_minmax(0,1fr)]">
        <div className="relative min-h-[170px] overflow-hidden border-b border-line bg-[var(--ds-bg-2)] md:border-b-0 md:border-r">
          {playbackUrl && !videoFailed ? (
            <video
              className="h-full min-h-[170px] w-full object-cover"
              controls
              preload="metadata"
              poster={thumbnailUrl || undefined}
              onError={() => setVideoFailed(true)}
              aria-label={`${title} 缓存播放`}
            >
              <source src={playbackUrl} />
            </video>
          ) : thumbnailUrl && !thumbnailFailed ? (
            hasOriginal ? (
              <a href={originalUrl} target="_blank" rel="noreferrer" className="group relative block h-full min-h-[170px]" aria-label={`打开 ${title} 原视频`}>
                {thumbnail}
              </a>
            ) : (
              <div className="group relative h-full min-h-[170px]">{thumbnail}</div>
            )
          ) : (
            <div className="flex h-full min-h-[170px] flex-col items-center justify-center gap-2 px-3 text-center text-muted">
              <Film size={22} />
              <span className="text-[10px]">缩略图与缓存播放均不可用</span>
              <span className="text-[9px]">{hasOriginal ? "原视频链接仍可回跳" : "原视频链接也未记录"}</span>
            </div>
          )}
          <span className="absolute left-2 top-2 rounded border border-white/15 bg-black/65 px-1.5 py-0.5 text-[9px] text-white backdrop-blur">
            外部市场样例 · {platformLabel(video)}
          </span>
          <span className="absolute bottom-2 left-2 rounded border border-white/15 bg-black/65 px-1.5 py-0.5 text-[8.5px] text-white backdrop-blur">
            {playbackUrl && !videoFailed ? "缓存播放" : thumbnailUrl && !thumbnailFailed ? "来源缩略图" : "无媒体预览"}
          </span>
          {playbackUrl ? (
            <span className="absolute right-2 top-2 rounded border border-white/15 bg-black/65 px-1.5 py-0.5 text-[9px] text-white backdrop-blur">
              {playbackSource === "r2" ? "R2 缓存" : playbackSource === "local_cache" ? "本地缓存" : "媒体缓存"}
            </span>
          ) : null}
        </div>

        <div className="flex min-w-0 flex-col p-3.5">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-0.5 text-[8.5px] text-accent">外部市场样例</span>
                <span className="rounded border border-line px-1.5 py-0.5 text-[8.5px] text-muted">{contentFormat(video)}</span>
              </div>
              <h3 className="line-clamp-2 text-[13px] font-semibold leading-snug text-ink">{title}</h3>
            </div>
            {hasOriginal ? (
              <a
                href={originalUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line bg-card px-2 py-1 text-[10px] text-ink-2 hover:border-line-strong hover:text-ink"
              >
                打开原视频 <ExternalLink size={10} />
              </a>
            ) : (
              <span className="shrink-0 rounded-md border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-2 py-1 text-[9px] text-warn">无原视频链接</span>
            )}
          </div>

          <p className="mt-2 text-[11px] leading-relaxed text-ink-2">
            {video.why_recommended || video.content_summary || "已入库为外部市场样例，推荐理由未记录。"}
          </p>

          <MetadataGrid item={video} stale={snapshotStale} source={sourceSummary} />

          <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[9.5px] text-muted">
            <span>播放 {metric(video.view_count)}</span>
            <span>互动 {metric(Number(video.like_count || 0) + Number(video.comment_count || 0))}</span>
            <span>Fit {video.fit_score == null ? "—" : Number(video.fit_score).toFixed(1)}</span>
            {video.platform_video_id ? <span>平台 ID {video.platform_video_id}</span> : <span>平台 ID 未记录</span>}
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2">
            <span className="mr-1 text-[8.5px] text-muted">证据来源</span>
            {sourceRefs.length ? sourceRefs.map((ref: Record<string, any>, index: number) => (
              <span key={`${ref.table}-${ref.id}-${index}`} className="rounded border border-line px-1.5 py-0.5 font-mono text-[8.5px] text-muted">
                {ref.table || "source"}:{ref.id ?? "—"}
              </span>
            )) : <span className="text-[8.5px] text-warn">证据表引用未记录</span>}
          </div>
        </div>
      </div>
    </article>
  );
}

function MarketSourceRow({ source, snapshotStale }: { source: Record<string, any>; snapshotStale: boolean }) {
  const url = itemUrl(source);
  const hasUrl = hasHttpUrl(url);
  const relation = String(source.relation_type || source.relationType || "");
  const title = String(source.title || source.name || url || "未命名来源");
  const isExternalPlatform = Boolean(platformKey(source)) && isExternalEvidence(source);
  const sourceExpired = source.source_status === "expired" || source.sourceStatus === "expired";
  return (
    <article className="px-3 py-3">
      <div className="flex min-w-0 items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            {isExternalPlatform ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-0.5 text-[8.5px] text-accent">外部市场样例</span> : null}
            {relation === "grounding" ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-good)_30%,transparent)] bg-good-soft px-1.5 py-0.5 text-[8.5px] text-good">直接引文</span> : null}
            {relation === "brand_context" ? <span className="rounded border border-line px-1.5 py-0.5 text-[8.5px] text-muted">关联来源 · 非直接引文</span> : null}
            {sourceExpired ? <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-1.5 py-0.5 text-[8.5px] text-warn">历史来源</span> : null}
          </div>
          <h4 className="text-[11px] font-medium leading-relaxed text-ink-2">{title}</h4>
          {isExternalPlatform ? <MetadataGrid item={source} stale={snapshotStale || sourceExpired} source={sourceRecordLabel(source)} /> : (
            <div className="mt-1.5 font-mono text-[8.5px] text-muted">来源 {sourceRecordLabel(source)} · 新鲜度 {itemFreshness(source, snapshotStale)}</div>
          )}
        </div>
        {hasUrl ? (
          <a href={url} target="_blank" rel="noreferrer" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line bg-card text-muted hover:text-ink" aria-label={`打开来源 ${title}`}>
            <ArrowUpRight size={12} />
          </a>
        ) : <span className="shrink-0 rounded border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] px-1.5 py-1 text-[8.5px] text-warn">无原始链接</span>}
      </div>
    </article>
  );
}

export function AITodayEvidenceModal({ insight, onClose, onOpenKolPool }: any) {
  const videos = (Array.isArray(insight?.recommendedVideos) ? insight.recommendedVideos : [])
    .filter((item: Record<string, any>) => isExternalEvidence(item));
  const sources = Array.isArray(insight?.sources) ? insight.sources : [];
  const contextOnlySources = sources.length > 0 && sources.every((source: Record<string, any>) =>
    String(source.relation_type || source.relationType || "") === "brand_context");
  const traceableSources = sources.filter((source: Record<string, any>) => hasHttpUrl(itemUrl(source)));
  const isStale = Boolean(insight?.isStale);
  const freshnessStatus = String(insight?.freshnessStatus || "unknown");
  const isFresh = freshnessStatus === "fresh" || /^新鲜/.test(String(insight?.freshnessLabel || ""));

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="ai-evidence-modal cockpit-modal fixed inset-0 z-[9999] flex items-center justify-center overflow-y-auto bg-black/65 p-3 backdrop-blur-lg md:p-4"
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.97, y: 14 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97 }}
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[94vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-line bg-card shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-today-evidence-title"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-4 py-4 md:px-5">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-panel text-accent">
              <Sparkles size={17} />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="ai-today-evidence-title" className="text-base font-semibold text-ink">AI Today 证据</h2>
                <span className="rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-0.5 text-[9px] text-accent">来源与外部市场样例</span>
                <span className={`rounded border px-1.5 py-0.5 text-[9px] ${isStale ? "border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft text-warn" : isFresh ? "border-[color:color-mix(in_srgb,var(--ds-good)_30%,transparent)] bg-good-soft text-good" : "border-line bg-panel text-muted"}`}>
                  {insight?.freshnessLabel || "时间未记录"}
                </span>
              </div>
              <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-ink-2">{insight?.todayDecision?.text || "暂无今日决策"}</p>
              <p className="mt-1 font-mono text-[9px] text-muted">
                snapshot {insight?.snapshotDate || "—"} · generated {insight?.generatedAt || "—"}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-panel text-muted hover:bg-accent-soft hover:text-ink"
            aria-label="关闭"
            title="关闭"
          >
            <X size={15} />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-4 md:p-5">
          {isStale ? (
            <div className="flex items-start gap-2 rounded-lg border border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft px-3 py-2.5 text-[11px] text-warn">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>该决策快照已过期。下方内容是历史外部市场样例与来源记录，不能代替今日市场的重新生成。</span>
            </div>
          ) : null}

          <section>
            <div className="mb-2.5 flex items-end justify-between gap-3">
              <div>
                <h3 className="text-[12px] font-semibold text-ink">外部市场样例</h3>
                <p className="mt-0.5 text-[10px] text-muted">来自 YouTube、TikTok、Instagram 或 Facebook 的匹配内容；不是 AI 结论的直接引文</p>
              </div>
              <span className="shrink-0 font-mono text-[10px] text-muted">{videos.length} examples</span>
            </div>
            {videos.length ? (
              <div className="space-y-2.5">
                {videos.map((video: Record<string, any>, index: number) => (
                  <VideoEvidenceCard key={video.evidence_id || `${video.content_url}-${index}`} video={video} snapshotStale={isStale} />
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-line py-10 text-center text-[11px] text-muted">
                暂无外部市场样例；平台、作者、发布时间与媒体预览均不可判定
              </div>
            )}
          </section>

          <section>
            <div className="mb-2.5 flex items-center gap-2">
              <Database size={13} className="text-muted" />
              <h3 className="text-[12px] font-semibold text-ink">市场来源</h3>
              <span className="font-mono text-[9px] text-muted">{traceableSources.length}/{sources.length} links</span>
            </div>
            {contextOnlySources ? (
              <div className="mb-2 rounded-md border border-[color:color-mix(in_srgb,var(--ds-warn)_25%,transparent)] bg-warn-soft px-2.5 py-2 text-[10px] leading-relaxed text-warn">
                该历史 AI 快照未保留当时的 Google 引文。下列是后续从市场信号库匹配的真实关联来源，不等于该快照的直接引文。
              </div>
            ) : null}
            {sources.length > 0 && traceableSources.length === 0 ? (
              <div className="mb-2 rounded-md border border-[color:color-mix(in_srgb,var(--ds-warn)_25%,transparent)] bg-warn-soft px-2.5 py-2 text-[10px] text-warn">
                有来源记录，但均未保留原始链接，当前无法从 UI 回跳核验。
              </div>
            ) : null}
            {sources.length ? (
              <div className="divide-y divide-[var(--ds-line)] overflow-hidden rounded-lg border border-line bg-panel">
                {sources.map((source: Record<string, any>, index: number) => (
                  <MarketSourceRow key={`${source.url || source.title || "source"}-${index}`} source={source} snapshotStale={isStale} />
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-line py-8 text-center text-[11px] text-warn">
                该快照未保留原始市场来源，无法核验直接引文
              </div>
            )}
          </section>
        </div>

        <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 md:px-5">
          <span className="text-[9px] text-muted">缩略图、缓存播放、原帖链接与 evidence ID 分开标记</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="rounded-lg border border-line bg-panel px-3 py-1.5 text-[10px] text-ink-2 hover:bg-accent-soft">关闭</button>
            <button
              type="button"
              onClick={onOpenKolPool}
              disabled={!onOpenKolPool}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[10px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              进入 KOL Pool <ArrowUpRight size={11} />
            </button>
          </div>
        </footer>
      </motion.div>
    </motion.div>
  );
}
