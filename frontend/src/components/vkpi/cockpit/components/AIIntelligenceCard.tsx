import React from "react";
import { motion } from "framer-motion";
import {
  Clock,
  ExternalLink,
  FileSearch,
  Film,
  Loader2,
  Play,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { useT } from "../lib/i18n";
import "./ai-evidence-cards.css";

type EvidenceCardVariant = "compact" | "standard" | "focus";

const PLATFORM_LABELS: Record<string, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
  facebook: "Facebook",
};

function hasHttpUrl(value: unknown) {
  return /^https?:\/\//i.test(String(value || "").trim());
}

function isExternalEvidence(item: Record<string, any>) {
  return String(item.content_origin || item.contentOrigin || "").trim().toLowerCase() === "external";
}

function platformKey(item: Record<string, any>) {
  const raw = [item.platform, item.provider, item.source, item.content_url, item.url]
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
  const url = String(item.content_url || item.url || "").toLowerCase();
  if (key === "youtube" && url.includes("/shorts/")) return "Shorts";
  if (key === "youtube" && (url.includes("watch?") || url.includes("youtu.be/"))) return "视频";
  if (key === "tiktok" && url.includes("/video/")) return "短视频";
  if (key === "instagram" && url.includes("/reel")) return "Reel";
  if (key === "instagram" && url.includes("/p/")) return "帖子";
  if (key === "facebook" && url.includes("/reel")) return "Reel";
  if (key === "facebook" && (url.includes("/video") || url.includes("fb.watch"))) return "视频";
  return "格式未记录";
}

function publishedValue(item: Record<string, any>) {
  return String(item.published_at || item.publish_date || item.posted_at || "").trim();
}

function dateLabel(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "未记录";
  const calendarDate = raw.match(/^\d{4}-\d{2}-\d{2}/)?.[0];
  if (calendarDate) return calendarDate;
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return parsed.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function ageLabel(value: unknown) {
  const raw = String(value || "").trim();
  if (!raw) return "新鲜度未知";
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return "新鲜度未知";
  const ageDays = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (ageDays < -1) return "发布时间待核验";
  if (ageDays <= 0) return "今天发布";
  if (ageDays === 1) return "1 天前";
  return `${ageDays} 天前`;
}

function evidenceFreshness(item: Record<string, any>, snapshotStale: boolean) {
  const own = String(item.freshness_label || item.freshnessLabel || "").trim() || ageLabel(publishedValue(item));
  return snapshotStale ? `快照过期 · ${own}` : own;
}

function authorLabel(item: Record<string, any>) {
  const name = String(item.creator_name || item.author_name || item.author || item.channel_name || "").trim();
  const handle = String(item.creator_handle || item.author_handle || "").trim().replace(/^@/, "");
  if (name && handle && name.toLowerCase() !== handle.toLowerCase()) return `${name} · @${handle}`;
  if (name) return name;
  if (handle) return `@${handle}`;
  return "作者未记录";
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
      const rawSpan = moduleElement?.style.getPropertyValue("--vkpi-module-span") || "";
      const parsedSpan = Number(rawSpan);
      const span = Number.isFinite(parsedSpan) && parsedSpan > 0 ? parsedSpan : null;
      const width = element.getBoundingClientRect().width;
      const gridTemplate = boardElement ? window.getComputedStyle(boardElement).gridTemplateColumns : "";
      const repeatedColumns = Number(gridTemplate.match(/repeat\(\s*(\d+)/)?.[1] || 0);
      const computedColumns = gridTemplate.split(/\s+/).filter((column) => column && column !== "none");
      const boardColumnCount = repeatedColumns || (computedColumns.length > 1 ? computedColumns.length : null);
      const effectiveSpan = boardColumnCount && boardColumnCount < 12 ? boardColumnCount : span || 4;
      let variant = explicitVariant || spanVariant(effectiveSpan);
      if (!explicitVariant && width > 0) {
        if (width < 520) variant = "compact";
        else if (width < 900) variant = "standard";
        else if (!moduleElement) variant = "focus";
      }
      setLayout((current) => current.variant === variant && current.span === span ? current : { variant, span });
    };

    update();
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    resizeObserver?.observe(element);
    const mutationObserver = moduleElement && typeof MutationObserver !== "undefined"
      ? new MutationObserver(update)
      : null;
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

function highlightedDecision(text: string, amount: unknown) {
  const token = String(amount || "").trim();
  if (!token || token === "--" || !text.includes(token)) return text;
  const parts = text.split(token);
  return parts.flatMap((part, index) => index === parts.length - 1
    ? [part]
    : [part, <span key={`${token}-${index}`} className="font-semibold text-warn">{token}</span>]);
}

function AdviceSection({ label, tone, rows }: { label: string; tone: string; rows: Array<{ text: string; detail?: string }> }) {
  return (
    <section className="min-w-0 rounded-lg border border-line bg-panel px-3 py-2.5">
      <div className={`mb-1.5 text-[9px] font-medium uppercase ${tone}`}>{label}</div>
      {rows.length ? rows.map((row, index) => (
        <div key={`${row.text}-${index}`} className="mb-1 flex min-w-0 items-start gap-1.5 text-[10.5px] last:mb-0">
          <span className={`mt-0.5 shrink-0 ${tone}`}>·</span>
          <span className="min-w-0 text-ink-2">
            {row.text}
            {row.detail ? <span className="ml-1 text-[9.5px] text-muted">{row.detail}</span> : null}
          </span>
        </div>
      )) : <div className="text-[10px] text-muted">无可用条目</div>}
    </section>
  );
}

export function AIIntelligenceCard({
  insight,
  onApprove,
  onLater,
  onRegenerate,
  regenerating,
  regenerationState,
  variant,
}: any) {
  const { t } = useT();
  const model = insight || {};
  const { ref, variant: layoutVariant, span } = useEvidenceCardLayout(variant);
  const confidence = Number(model.confidence);
  const confLabel = Number.isFinite(confidence) && confidence > 0 ? `${confidence}% conf · ` : "";
  const videos = (Array.isArray(model.recommendedVideos) ? model.recommendedVideos : [])
    .filter((item: Record<string, any>) => isExternalEvidence(item));
  const sources = Array.isArray(model.sources) ? model.sources : [];
  const featuredVideo = videos[0] || null;
  const [featuredMediaFailed, setFeaturedMediaFailed] = React.useState(false);

  const isStale = Boolean(model.isStale);
  const freshnessStatus = String(model.freshnessStatus || "unknown");
  const updatedLabel = String(model.updatedLabel || model.freshnessLabel || "更新时间未记录");
  const statusClass = isStale
    ? "border-[color:color-mix(in_srgb,var(--ds-warn)_30%,transparent)] bg-warn-soft text-warn"
    : freshnessStatus === "fresh"
      ? "border-[color:color-mix(in_srgb,var(--ds-good)_30%,transparent)] bg-good-soft text-good"
      : "border-line bg-card text-muted";
  const hasDirectMarketSource = sources.some((source: Record<string, any>) => hasHttpUrl(source?.url || source?.content_url));
  const contextOnlySources = sources.length > 0 && sources.every((source: Record<string, any>) =>
    String(source?.relation_type || source?.relationType || "") === "brand_context");
  const sourceStatus = !sources.length
    ? "未保留市场来源"
    : contextOnlySources
      ? `${sources.length} 条关联来源 · 非直接引文`
      : hasDirectMarketSource
        ? `${sources.length} 条市场来源可回跳`
        : `${sources.length} 条来源记录 · 无原始链接`;
  const sourceNeedsWarning = !sources.length || contextOnlySources || !hasDirectMarketSource;
  const latestAttempt = model.latestAttempt && typeof model.latestAttempt === "object"
    ? model.latestAttempt
    : null;
  const latestAttemptStatus = String(latestAttempt?.status || "").toLowerCase();
  const latestAttemptFailed = Boolean(latestAttempt)
    && !["ok", "ready", "success"].includes(latestAttemptStatus);
  const attemptStatusLabel = ({
    invalid: "合同未通过",
    degraded: "降级",
    failed: "失败",
    budget_blocked: "预算拦截",
  } as Record<string, string>)[latestAttemptStatus] || latestAttemptStatus || "未知状态";
  const latestAttemptProvider = String(latestAttempt?.provider || "unknown");
  const latestAttemptReason = String(latestAttempt?.reason || "ai_today_not_ready");
  const reasonLabel = ({
    invalid_result_contract: "结果合同未通过",
    partial_result_contract: "结果字段不完整",
    budget_blocked: "预算闸已拦截",
    claude_fallback_without_grounding: "Claude 回退缺少可回溯来源",
    invalid_grounding_contract: "来源合同未通过",
    no_grounded_citations: "未取得可回溯引文",
  } as Record<string, string>)[latestAttemptReason] || latestAttemptReason;
  const regenerationPhase = String(regenerationState?.phase || "idle");
  const regenerationMessage = String(regenerationState?.message || "").trim();
  const regenerationTone = regenerationPhase === "success"
    ? "border-good bg-good-soft text-good"
    : regenerationPhase === "degraded"
      ? "border-warn bg-warn-soft text-warn"
      : regenerationPhase === "error"
        ? "border-crit bg-crit-soft text-crit"
        : "border-accent bg-accent-soft text-accent";

  const decision = model.todayDecision || {};
  const decisionText = String(decision.text || "暂无今日决策");
  const displayLimit = layoutVariant === "compact" ? 2 : layoutVariant === "standard" ? 3 : 4;
  const strengthen = (Array.isArray(model.strengthen) ? model.strengthen : []).slice(0, displayLimit).map((item: any) => ({
    text: String(item?.text || item || ""),
    detail: String(item?.detail || ""),
  })).filter((item: { text: string }) => item.text);
  const weaken = (Array.isArray(model.weaken) ? model.weaken : []).slice(0, displayLimit).map((item: any) => ({
    text: String(item?.text || item || ""),
    detail: String(item?.detail || ""),
  })).filter((item: { text: string }) => item.text);
  const todayContent = (Array.isArray(model.todayContent) ? model.todayContent : []).slice(0, displayLimit).map((item: any) => ({
    text: String(item || ""),
  })).filter((item: { text: string }) => item.text);
  const productRecommendations = (Array.isArray(model.productRecommendations) ? model.productRecommendations : [])
    .slice(0, displayLimit)
    .map((item: any) => ({ text: String(item || "") }))
    .filter((item: { text: string }) => item.text);
  const contentRecommendations = (Array.isArray(model.contentRecommendations) ? model.contentRecommendations : [])
    .slice(0, displayLimit)
    .map((item: any) => ({ text: String(item || "") }))
    .filter((item: { text: string }) => item.text);
  const videoRecommendations = (Array.isArray(model.videoRecommendations) ? model.videoRecommendations : [])
    .slice(0, displayLimit)
    .map((item: any) => ({ text: String(item || "") }))
    .filter((item: { text: string }) => item.text);

  const video = featuredVideo || {};
  const videoUrl = String(video.content_url || video.url || "");
  const thumbnailUrl = proxiedImageUrl(
    video.cached_thumbnail_url
      || video.best_thumbnail
      || video.thumbnail_url
      || video.youtube_thumbnail_url
      || "",
  );
  React.useEffect(() => setFeaturedMediaFailed(false), [thumbnailUrl]);
  const sourceRefs = Array.isArray(video.source_refs) ? video.source_refs : [];
  const videoPlatform = platformLabel(video);
  const videoFormat = contentFormat(video);
  const videoAuthor = authorLabel(video);
  const videoPublished = dateLabel(publishedValue(video));
  const videoFreshness = evidenceFreshness(video, isStale);
  const videoSource = sourceRefs.length
    ? sourceRefs.slice(0, 2).map((source: Record<string, any>) => `${source.table || "source"}:${source.id ?? "—"}`).join(" · ")
    : hasHttpUrl(videoUrl)
      ? `${videoPlatform} 原帖`
      : "来源未记录";

  return (
    <motion.article
      ref={ref as React.Ref<HTMLElement>}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.18 }}
      className={`ai-evidence-card is-${layoutVariant} flex h-full flex-col rounded-xl border border-line p-4`}
      data-layout-variant={layoutVariant}
      data-column-span={span || undefined}
      style={{ boxShadow: "var(--ds-shadow)" }}
    >
      <header className="mb-3 flex min-w-0 items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <Sparkles size={14} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-ink">AI Today</h3>
            <p className="mt-0.5 text-[9px] text-muted">决策快照与可回溯外部样例</p>
          </div>
        </div>
        <span className={`shrink-0 rounded-md border px-2 py-0.5 text-[9px] font-medium ${statusClass}`}>
          {confLabel}{updatedLabel}
        </span>
      </header>

      <div className="ai-evidence-card__body flex-1">
        <section className="min-w-0 rounded-lg border border-line bg-panel px-3 py-3">
          <div className="mb-1.5 text-[9px] font-medium uppercase text-muted">今日重点决策</div>
          <p className="text-[12px] leading-relaxed text-ink-2">
            {highlightedDecision(decisionText, decision.amount)}
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-muted">{t("理由:")}{decision.reason || "未记录决策依据"}</p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={onApprove}
              disabled={!onApprove}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[10px] font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FileSearch size={10} /> {decision.primaryAction || "查看来源"}
            </button>
            <button
              type="button"
              onClick={onLater || undefined}
              disabled={!onLater}
              title={onLater ? "稍后处理" : "待接入真实提醒写入接口"}
              className="inline-flex items-center gap-1 rounded-md border border-line-strong px-2.5 py-1 text-[10px] text-ink-2 hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Clock size={10} /> {decision.secondaryAction || "稍后处理"}
            </button>
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <div>
              <div className="text-[9px] font-medium uppercase text-accent">外部市场样例</div>
              <div className="mt-0.5 text-[8.5px] text-muted">匹配创意方向，不等同于决策的直接引文</div>
            </div>
            <span className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[8px] text-muted">{videos.length} 个样例</span>
          </div>

          {featuredVideo ? (
            <div className="ai-evidence-card__example overflow-hidden rounded-lg border border-line bg-panel">
              <button
                type="button"
                onClick={onApprove}
                disabled={!onApprove}
                className="group relative min-h-[96px] overflow-hidden bg-[var(--ds-bg-2)] text-left disabled:cursor-default"
                aria-label="查看外部市场样例详情"
              >
                {thumbnailUrl && !featuredMediaFailed ? (
                  <img
                    src={thumbnailUrl}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                    onError={() => setFeaturedMediaFailed(true)}
                  />
                ) : (
                  <span
                    className="absolute inset-0 flex items-center justify-center text-muted"
                    role="img"
                    aria-label={featuredMediaFailed ? "缩略图加载失败，未使用原始平台图片" : "该外部样例无可用缩略图"}
                  >
                    <Film size={18} />
                  </span>
                )}
                <span className="absolute inset-0 flex items-center justify-center bg-black/10">
                  <span className="flex h-8 w-8 items-center justify-center rounded-full border border-white/30 bg-black/60 text-white">
                    <Play size={13} fill="currentColor" />
                  </span>
                </span>
                <span className="absolute bottom-1 left-1 rounded border border-white/20 bg-black/65 px-1 py-px text-[8px] text-white">
                  {thumbnailUrl && !featuredMediaFailed ? "缩略图" : featuredMediaFailed ? "缩略图不可用" : "无预览"}
                </span>
              </button>

              <div className="min-w-0 p-2.5">
                <div className="mb-1 flex min-w-0 items-start justify-between gap-2">
                  <h4 className="line-clamp-2 min-w-0 text-[11px] font-semibold leading-snug text-ink">{video.title || "未命名外部样例"}</h4>
                  <span className="shrink-0 rounded border border-[color:color-mix(in_srgb,var(--ds-accent)_25%,transparent)] bg-accent-soft px-1.5 py-0.5 text-[8px] text-accent">外部</span>
                </div>
                <dl className="ai-evidence-card__metadata mt-2">
                  <div><dt>平台</dt><dd>{videoPlatform}</dd></div>
                  <div><dt>内容格式</dt><dd>{videoFormat}</dd></div>
                  <div><dt>作者</dt><dd>{videoAuthor}</dd></div>
                  <div><dt>发布</dt><dd>{videoPublished}</dd></div>
                  <div><dt>新鲜度</dt><dd className={isStale ? "text-warn" : ""}>{videoFreshness}</dd></div>
                  <div><dt>来源</dt><dd>{videoSource}</dd></div>
                </dl>
                <div className="mt-2 flex min-w-0 items-center justify-between gap-2 border-t border-line pt-2 text-[8.5px]">
                  <span className="min-w-0 text-muted">{sourceRefs.length ? "证据表可查" : "证据表未记录"}</span>
                  {hasHttpUrl(videoUrl) ? (
                    <a href={videoUrl} target="_blank" rel="noreferrer" className="inline-flex shrink-0 items-center gap-1 text-ink-2 hover:text-ink">
                      打开原帖 <ExternalLink size={9} />
                    </a>
                  ) : <span className="shrink-0 text-warn">无原帖链接</span>}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex min-h-[96px] items-center justify-center rounded-lg border border-dashed border-line bg-panel px-4 text-center text-[10px] text-muted" role="status">
              {sources.length ? `暂无外部视频样例 · 已保留 ${sources.length} 条市场来源` : "暂无外部市场样例，且未保留可回溯来源"}
            </div>
          )}
        </section>

        <div className="ai-evidence-card__details">
          <AdviceSection label={model.strengthenLabel || "加强"} tone="text-good" rows={strengthen} />
          {productRecommendations.length ? (
            <AdviceSection label={model.productRecommendationLabel || "产品推荐"} tone="text-accent" rows={productRecommendations} />
          ) : null}
          {contentRecommendations.length ? (
            <AdviceSection label={model.contentRecommendationLabel || "内容打法"} tone="text-good" rows={contentRecommendations} />
          ) : null}
          {videoRecommendations.length ? (
            <AdviceSection label="视频推荐理由" tone="text-accent" rows={videoRecommendations} />
          ) : null}
          {weaken.length ? <AdviceSection label="减弱" tone="text-warn" rows={weaken} /> : null}
          <AdviceSection label={model.todayContentLabel || "今天适合发"} tone="text-accent" rows={todayContent} />
        </div>
      </div>

      {latestAttemptFailed ? (
        <div
          role="status"
          data-ai-latest-attempt-status={latestAttemptStatus || "unknown"}
          className="mt-3 rounded-lg border border-warn bg-warn-soft px-2.5 py-2 text-[9.5px] leading-relaxed text-warn"
          title={`${latestAttemptProvider} · ${latestAttemptReason}`}
        >
          <div className="font-medium">
            最近生成尝试 · {latestAttempt.attemptedLabel || "时间未记录"} · {attemptStatusLabel} · {latestAttemptProvider}
          </div>
          <div className="mt-0.5 opacity-90">
            原因：{reasonLabel}{reasonLabel !== latestAttemptReason ? `（${latestAttemptReason}）` : ""}
            {latestAttempt.generationStatus ? ` · ${latestAttempt.generationStatus}` : ""}
          </div>
        </div>
      ) : null}

      {regenerationPhase !== "idle" && regenerationMessage ? (
        <div
          role="status"
          aria-live="polite"
          data-ai-regeneration-phase={regenerationPhase}
          className={`mt-3 rounded-lg border px-2.5 py-2 text-[9.5px] leading-relaxed ${regenerationTone}`}
        >
          {regenerationMessage}
        </div>
      ) : null}

      <footer className="mt-3 flex min-w-0 items-center justify-between gap-3 border-t border-line pt-2.5">
        <span className={`min-w-0 text-[9px] ${isStale || sourceNeedsWarning ? "text-warn" : "text-muted"}`}>
          {isStale ? "历史快照 · " : ""}{sourceStatus}
        </span>
        {onRegenerate ? (
          <button
            type="button"
            onClick={onRegenerate}
            disabled={regenerating}
            className="inline-flex shrink-0 items-center gap-1 text-[10px] text-muted hover:text-ink disabled:opacity-50"
            title="重新生成 AI Today"
          >
            {regenerating ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
            {regenerating ? "生成中" : "重新生成"}
          </button>
        ) : null}
      </footer>
    </motion.article>
  );
}
