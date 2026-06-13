import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, Clock3, Database, Link2, Loader2, Search, Sparkles, UserPlus, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  getKolSearchSession,
  listKolSearchHistory,
  smartKolSearchProfileAdvanceJob,
  smartKolSearch,
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
  type VkpiKolSmartSearchProfileAdvanceResponse,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { getKolVideoAnalysisCache, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";

type Mode = "idle" | "url" | "text";
type State = "idle" | "loading" | "ready" | "executing" | "error";
type Row = Record<string, unknown>;
const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";

function cleanText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

// YouTube embed:复用 KOLDetailDrawer 的 youtube-nocookie 格式(B:视频结果区可播放)。
function youtubeEmbedUrl(videoId: string): string {
  const id = String(videoId || "").trim();
  if (!id) return "";
  const origin = typeof window !== "undefined" && window.location?.origin
    ? `&origin=${encodeURIComponent(window.location.origin)}`
    : "";
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?rel=0&playsinline=1&modestbranding=1${origin}`;
}

function display(value: unknown, fallback = "--"): string {
  const text = cleanText(value);
  return text || fallback;
}

function numberLabel(value: unknown): string {
  const next = Number(value);
  if (!Number.isFinite(next) || next <= 0) return "";
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 10_000) return `${Math.round(next / 1_000)}K`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

function durationLabel(value: unknown): string {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const totalSeconds = Math.max(1, Math.round(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function detectMode(input: string): Mode {
  const value = cleanText(input);
  if (!value) return "idle";
  try {
    const parsed = new URL(value.includes("://") ? value : `https://${value}`);
    const supportedProtocol = parsed.protocol === "http:" || parsed.protocol === "https:";
    return supportedProtocol && parsed.hostname.includes(".") ? "url" : "text";
  } catch {
    return "text";
  }
}

function sessionIdFrom(value: unknown): number | undefined {
  const record = asRecord(value);
  const raw = record.session_id ?? record.id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function terminalSessionStatus(value: unknown): boolean {
  const status = cleanText(value).toLowerCase();
  return ["ready", "partial", "failed", "done", "blocked", "cancelled", "canceled"].includes(status);
}

function actionDescription(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  return cleanText(record.description || record.label || record.code);
}

function urlTypeLabel(value: unknown): string {
  const text = cleanText(value);
  if (text === "profile") return "Profile URL";
  if (text === "video") return "Video URL";
  if (text === "unknown") return "Unknown URL";
  return text || "--";
}

function videoExecutionDone(status: unknown): boolean {
  return ["queued", "already_queued", "already_analyzed", "ready", "partial"].includes(cleanText(status));
}

function recallTopItems(response: VkpiKolRecallResponse | null): VkpiKolRecallItem[] {
  if (!response) return [];
  const creator = Array.isArray(response.buckets?.creator) ? response.buckets.creator : [];
  const reviewer = Array.isArray(response.buckets?.reviewer) ? response.buckets.reviewer : [];
  return [...creator.slice(0, 3), ...reviewer.slice(0, 2)];
}

function historySessionId(value: unknown): number | undefined {
  const record = asRecord(value);
  const raw = record.id ?? record.session_id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function sessionItems(session: VkpiKolSearchHistoryItem): Row[] {
  const items = Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.items_preview)
      ? session.items_preview
      : [];
  return items.map((item) => asRecord(item));
}

function sessionAdvanceCounts(session: VkpiKolSearchHistoryItem | null): Row {
  const summary = asRecord(session?.result_summary);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return asRecord(batch.counts || smartJob.advance_counts);
}

function isSearchSessionTerminal(session: VkpiKolSearchHistoryItem): boolean {
  if (terminalSessionStatus(session.status)) return true;
  const summary = asRecord(session.result_summary);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return terminalSessionStatus(batch.status) || terminalSessionStatus(smartJob.status) || terminalSessionStatus(smartJob.advance_status);
}

function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
  const creator: VkpiKolRecallItem[] = [];
  const reviewer: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
    // 三框:框2 只放库内召回(recall_candidate);new_creator(全网发现)归框3,见 discoveryItemsFromSession。
    if (cleanText(item.item_type) === "new_creator") return;
    const payload = asRecord(item.payload);
    const bucket: "creator" | "reviewer" = cleanText(payload.bucket) === "reviewer" ? "reviewer" : "creator";
    const row = {
      bucket,
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || item.item_type, "creator"),
      followers: Number(payload.followers || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.recall_rank_score ?? payload.vector_score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: bucket === "reviewer" ? "测评号" : "创作者",
      creator_type_score: bucket === "creator" ? 1 : 0,
      reviewer_type_score: bucket === "reviewer" ? 1 : 0,
      recall_reason: cleanText(payload.evidence || payload.sample_title),
      why_fit: cleanText(payload.why_fit),
      source_fields: payload,
    } satisfies VkpiKolRecallItem;
    if (bucket === "reviewer") reviewer.push(row);
    else creator.push(row);
  });
  const summary = asRecord(session.result_summary);
  const querySummary = asRecord(summary.query);
  const diagnostics = asRecord(summary.diagnostics);
  return {
    method: "search_session_history",
    query: { query_text: display(querySummary.query_text || summary.query || session.query_text, "") },
    ratio: {
      creator_quota: creator.length,
      reviewer_quota: reviewer.length,
      policy: "history",
      mixed_policy: "history",
      dedupe: true,
    },
    items: [...creator, ...reviewer],
    buckets: { creator, reviewer },
    diagnostics: {
      ...diagnostics,
      candidate_count: Number(diagnostics.candidate_count ?? session.item_count ?? creator.length + reviewer.length),
      creator_returned: Number(diagnostics.creator_returned ?? creator.length),
      reviewer_returned: Number(diagnostics.reviewer_returned ?? reviewer.length),
      returned_count: creator.length + reviewer.length,
    },
  } satisfies VkpiKolRecallResponse;
}

// 三框·框3:从会话抽 new_creator(Apify+平台发现)项,带头像/用户名/平台。
function discoveryItemsFromSession(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
  if (!session) return [];
  const out: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
    if (cleanText(item.item_type) !== "new_creator") return;
    const payload = asRecord(item.payload);
    out.push({
      bucket: "creator",
      kol_pool_id: Number(item.kol_pool_id || payload.kol_pool_id || 0),
      handle: display(payload.handle || payload.display_name || payload.channel_name, "unknown"),
      display_name: cleanText(payload.display_name || payload.channel_name || payload.handle),
      platform: cleanText(payload.platform),
      profile_type: display(payload.profile_type || "creator", "creator"),
      followers: Number(payload.followers || payload.avg_views || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: "全网发现",
      creator_type_score: 1,
      reviewer_type_score: 0,
      recall_reason: cleanText(payload.sample_title || payload.evidence),
      why_fit: cleanText(payload.why_fit),
      source_fields: payload,
    } satisfies VkpiKolRecallItem);
  });
  return out;
}

function urlResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolUrlDeepCrawlResponse | null {
  const item = sessionItems(session).find((entry) => cleanText(entry.item_type).startsWith("url_")) || sessionItems(session)[0];
  if (!item) return null;
  const payload = asRecord(item.payload);
  const summary = asRecord(session.result_summary);
  const videoFlow = asRecord(payload.video_flow);
  const profileFlow = asRecord(payload.profile_flow);
  const analysis = asRecord(payload.analysis || summary.analysis);
  const jobLastError = cleanText(payload.job_last_error || summary.job_last_error || payload.search_session_last_error);
  const jobStatus = cleanText(payload.job_status || payload.search_session_last_job_status || item.status);
  const itemStatus = cleanText(item.status || summary.item_status || videoFlow.status || profileFlow.status);
  const urlType = cleanText(payload.url_type || session.query_type).includes("video") ? "video" : cleanText(payload.url_type || session.query_type).includes("profile") ? "profile" : "unknown";
  const terminal = terminalSessionStatus(session.status) || terminalSessionStatus(itemStatus);
  const nextAction = jobLastError
    ? "主任务已同步；部分富化/分析有错误，可查看错误并重试。"
    : terminal
      ? "任务已完成，结果已回填。"
      : "任务正在队列中运行，完成后会自动回填。";
  return {
    method: "search_session_history",
    execute: Boolean(summary.execute || terminal || ["queued", "running", "already_queued"].includes(itemStatus)),
    url: {
      input: session.query_text,
      normalized: cleanText(item.source_url || session.query_text),
    },
    url_type: urlType,
    platform: cleanText(payload.platform),
    handle: cleanText(payload.handle),
    channel_id: cleanText(payload.channel_id),
    video_id: cleanText(payload.video_id),
    in_pool: Boolean(payload.in_pool || item.kol_pool_id),
    matched_kol_pool_id: Number(payload.matched_kol_pool_id || item.kol_pool_id) || null,
    next_action: nextAction,
    profile_flow: profileFlow,
    video_flow: {
      ...videoFlow,
      status: itemStatus || cleanText(videoFlow.status),
      evidence_id: Number(item.evidence_id || videoFlow.evidence_id) || null,
      job_last_error: jobLastError,
      job_status: jobStatus,
      analysis,
    },
    creator_identity: asRecord(payload.creator_identity),
    video_metadata: asRecord(payload.video_metadata),
    search_session: { id: session.id, session_id: session.id, status: session.status, item_status: itemStatus },
    safety: { viltrox_fit_score_untouched: Boolean(payload.viltrox_fit_score_untouched ?? summary.viltrox_fit_score_untouched) },
  };
}

function historyKindLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  if (type === "url_video") return "视频 URL";
  if (type === "url_profile") return "账号 URL";
  if (type === "text_recall") return "查找";
  return "历史";
}

function HistoryStrip({
  items,
  loading,
  onOpen,
}: {
  items: VkpiKolSearchHistoryItem[];
  loading: boolean;
  onOpen: (session: VkpiKolSearchHistoryItem) => void;
}) {
  if (!items.length && !loading) return null;
  return (
    <div className="mt-2 rounded-lg border border-white/[0.055] bg-black/15 px-2.5 py-2">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5 text-[10px] font-medium text-slate-300">
          <Clock3 size={11} className="text-slate-500" />
          最近历史
        </div>
        {loading ? <span className="text-[9.5px] text-slate-600">同步中</span> : null}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.slice(0, 5).map((item) => {
          const sessionId = historySessionId(item);
          const label = display(item.query_text, `session #${sessionId || "--"}`);
          return (
            <button
              key={`${sessionId || label}-${item.updated_at || item.created_at || ""}`}
              type="button"
              onClick={() => onOpen(item)}
              className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-white/[0.07] bg-white/[0.025] px-2 py-1 text-[10px] text-slate-400 transition-colors hover:border-cyan-300/25 hover:text-cyan-100"
              title={label}
            >
              <span className="shrink-0 text-slate-600">{historyKindLabel(item)}</span>
              <span className="max-w-[220px] truncate">{label}</span>
              <span className="shrink-0 text-slate-600">{item.status || "ready"}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// 问题5 UI:相关度按名次分档(列表已按分排序,名次=相关度强弱),裸 score 进 title 供细看,
// 避免向量分都 <0.5 时全显「相关」无区分。
function relevanceTier(index: number): { label: string; cls: string; dot: string } {
  if (index <= 1) return { label: "高相关", cls: "border-emerald-300/35 bg-emerald-400/[0.10] text-emerald-100", dot: "#34d399" };
  if (index <= 3) return { label: "中相关", cls: "border-cyan-300/30 bg-cyan-400/[0.07] text-cyan-100", dot: "#22d3ee" };
  return { label: "相关", cls: "border-white/[0.08] bg-white/[0.02] text-slate-400", dot: "#64748b" };
}

function RecallMiniItem({
  item,
  index,
  onOpen,
}: {
  item: VkpiKolRecallItem;
  index: number;
  onOpen?: (item: VkpiKolRecallItem) => void;
}) {
  const [imgError, setImgError] = useState(false);
  const avatar = proxiedImageUrl(item.avatar_url);
  const name = display(item.handle || item.display_name || `KOL #${item.kol_pool_id}`);
  const followers = numberLabel(item.followers);
  const score = Number(item.recall_rank_score ?? item.vector_score ?? 0);
  const tier = relevanceTier(index);
  const showImg = Boolean(avatar) && !imgError;
  const whyFit = cleanText(item.why_fit);
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item)}
      className="group flex min-w-0 items-start gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.015] px-2.5 py-2 text-left transition-all hover:border-cyan-300/25 hover:bg-cyan-400/[0.04] focus:outline-none focus:ring-1 focus:ring-cyan-300/30"
      title={whyFit ? `${whyFit} · 相关度 ${score.toFixed(3)}` : `打开 KOL 详情 · 相关度 ${score.toFixed(3)}`}
    >
      <span className="mt-1 w-3.5 shrink-0 text-center text-[9px] font-medium tabular-nums text-slate-600">{index}</span>
      <span
        className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full text-[12px] font-bold text-white"
        style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
      >
        {showImg ? (
          <img
            src={avatar}
            alt=""
            className="h-full w-full rounded-full object-cover"
            referrerPolicy="no-referrer"
            onError={() => setImgError(true)}
          />
        ) : (
          name.slice(0, 1).toUpperCase()
        )}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-[11.5px] font-medium text-slate-100 group-hover:text-white">{name}</span>
          {followers ? (
            <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers}</span>
          ) : null}
        </span>
        <span className="mt-0.5 block truncate text-[9.5px] text-slate-500">
          {display(item.platform, "unknown")} · {item.type_label || item.profile_type || "profile"}
        </span>
        {whyFit ? (
          <span className="mt-1 line-clamp-2 block text-[10px] leading-snug text-cyan-200/85">{whyFit}</span>
        ) : null}
      </span>
      <span className={`mt-1 flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${tier.cls}`}>
        <span className="h-1 w-1 rounded-full" style={{ background: tier.dot }} />
        {tier.label}
      </span>
    </button>
  );
}

function PlanPills({ plan }: { plan: Row }) {
  const searchQuery = display(plan.search_query);
  const persona = display(plan.target_persona, "");
  const provider = display(plan.provider, "rule_v0");
  const focus = Array.isArray(plan.product_focus) ? plan.product_focus.map(cleanText).filter(Boolean).slice(0, 4) : [];
  return (
    <div className="mb-2 rounded-md border border-cyan-300/12 bg-cyan-400/[0.045] px-2.5 py-2">
      <div className="mb-1 flex flex-wrap items-center gap-1.5 text-[9.5px] text-cyan-100">
        <span className="rounded border border-cyan-300/15 px-1.5 py-0.5">LLM 查询计划</span>
        <span className="text-slate-500">{provider}</span>
      </div>
      <div className="truncate text-[10.5px] text-slate-300">{searchQuery}</div>
      {persona ? <div className="mt-1 truncate text-[10px] text-slate-500">{persona}</div> : null}
      {focus.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {focus.map((item) => (
            <span key={item} className="rounded border border-white/[0.07] bg-black/20 px-1.5 py-0.5 text-[9.5px] text-slate-400">
              {item}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ProfileInfoCard({ data }: { data: Row }) {
  const [imgError, setImgError] = useState(false);
  const avatar = proxiedImageUrl(cleanText(data.avatar_url));
  const handle = cleanText(data.handle);
  const platform = cleanText(data.platform);
  const name = display(handle || platform || "账户");
  const followers = numberLabel(data.followers);
  const posts = numberLabel(data.posts_count);
  const bio = cleanText(data.bio);
  const profileUrl = cleanText(data.profile_url);
  const showImg = Boolean(avatar) && !imgError;
  return (
    <div className="mt-2 flex items-start gap-3 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2">
      <span
        className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-[14px] font-bold text-white"
        style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
      >
        {showImg ? (
          <img
            src={avatar}
            alt=""
            className="h-full w-full rounded-full object-cover"
            referrerPolicy="no-referrer"
            onError={() => setImgError(true)}
          />
        ) : (
          name.slice(0, 1).toUpperCase()
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-[12px] font-medium text-slate-100">{name}</span>
          {platform ? (
            <span className="shrink-0 rounded border border-white/[0.08] px-1 text-[9px] text-slate-400">{platform}</span>
          ) : null}
          {followers ? (
            <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers} 粉</span>
          ) : null}
          {posts ? (
            <span className="shrink-0 rounded bg-cyan-400/[0.10] px-1 text-[9px] font-semibold text-cyan-200/90">{posts} 帖</span>
          ) : null}
        </div>
        {bio ? (
          <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{bio}</p>
        ) : null}
        {profileUrl ? (
          <a
            href={profileUrl}
            target="_blank"
            rel="noreferrer noopener"
            className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
          >
            {profileUrl}
          </a>
        ) : null}
      </div>
    </div>
  );
}

// A·上框:视频 URL 的创作者账号信息卡。复用 ProfileInfoCard 头像骨架(proxiedImageUrl + onError 渐变圆兜底),
// 数据取 creator_identity,缺失字段用 video_metadata 兜底;点开展开抽屉看该用户全部字段。
function VideoCreatorCard({ creator, metadata }: { creator: Row; metadata: Row }) {
  const [imgError, setImgError] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const avatar = proxiedImageUrl(cleanText(creator.avatar_url));
  const handle = cleanText(creator.handle || creator.display_name || creator.channel_name || metadata.channel_name);
  const platform = cleanText(creator.platform || metadata.platform);
  const channelId = cleanText(creator.channel_id || metadata.channel_id);
  const name = display(handle || channelId || "创作者");
  const followers = numberLabel(creator.followers ?? creator.subscriber_count);
  const bio = cleanText(creator.bio || creator.description || metadata.description);
  const profileUrl = cleanText(creator.profile_url || creator.channel_url);
  const showImg = Boolean(avatar) && !imgError;
  // 全部字段(creator_identity 优先,video_metadata 兜底),空值过滤。
  const allFields = Object.entries({ ...metadata, ...creator })
    .map(([key, value]) => [key, cleanText(value)] as const)
    .filter(([, value]) => Boolean(value));
  return (
    <div className="mt-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2">
      <div className="flex items-start gap-3">
        <span
          className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-[14px] font-bold text-white"
          style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
        >
          {showImg ? (
            <img
              src={avatar}
              alt=""
              className="h-full w-full rounded-full object-cover"
              referrerPolicy="no-referrer"
              onError={() => setImgError(true)}
            />
          ) : (
            name.slice(0, 1).toUpperCase()
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[12px] font-medium text-slate-100">{name}</span>
            {platform ? (
              <span className="shrink-0 rounded border border-white/[0.08] px-1 text-[9px] text-slate-400">{platform}</span>
            ) : null}
            {followers ? (
              <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers} 粉</span>
            ) : null}
          </div>
          {bio ? (
            <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{bio}</p>
          ) : null}
          {profileUrl ? (
            <a
              href={profileUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
            >
              {profileUrl}
            </a>
          ) : null}
        </div>
        {allFields.length ? (
          <button
            type="button"
            onClick={() => setExpanded((cur) => !cur)}
            className="shrink-0 rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-cyan-300/30 hover:text-cyan-100"
          >{expanded ? "收起" : "点开全部字段"}</button>
        ) : null}
      </div>
      {expanded && allFields.length ? (
        <div className="mt-2 grid gap-x-3 gap-y-1 border-t border-white/[0.06] pt-2 text-[10px] sm:grid-cols-2">
          {allFields.map(([key, value]) => (
            <div key={key} className="flex min-w-0 gap-1.5">
              <span className="shrink-0 text-slate-600">{key}</span>
              <span className="min-w-0 flex-1 truncate text-slate-300" title={value}>{value}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// A·下框:时间戳分镜 + 内容概述。读 final_v1 cache 的 layer1_visual_content.scene_timeline / content_summary。
// 复用 KOLVideoAnalysisPanel 的 sceneTimeline 行结构;缺则静默不渲染(降级,绝不报错占位)。
function sceneTimelineRowsLocal(value: unknown, max = 8): { key: string; timestamp: string; what: string }[] {
  if (!Array.isArray(value)) return [];
  return value.map((item, index) => {
    const record = asRecord(item);
    return {
      key: `${cleanText(record.timestamp) || "scene"}-${index}`,
      timestamp: cleanText(record.timestamp),
      what: cleanText(record.what ?? record.scene ?? record.content),
    };
  }).filter((row) => row.timestamp || row.what).slice(0, max);
}

function VideoSceneAnalysis({ apiToken, evidenceId }: { apiToken: string; evidenceId: string }) {
  const [entry, setEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  useEffect(() => {
    let cancelled = false;
    setEntry(null);
    if (!apiToken || !evidenceId) return undefined;
    getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1")
      .then((res) => {
        if (cancelled) return;
        if (res.state === "ready" && res.entry) setEntry(res.entry);
      })
      .catch(() => {
        // 静默降级:无缓存/读取失败则不渲染分析框,不打断视频展示。
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, evidenceId]);
  const result = asRecord(entry?.result);
  const payload = asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
  const layer1 = asRecord(payload.layer1_visual_content);
  const contentSummary = cleanText(layer1.content_summary);
  const sceneTimeline = sceneTimelineRowsLocal(layer1.scene_timeline);
  if (!contentSummary && !sceneTimeline.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {contentSummary ? (
        <div className="rounded-md border border-white/[0.05] bg-white/[0.02] px-2.5 py-2">
          <div className="mb-1 text-[9px] uppercase tracking-wider text-slate-500">内容概述</div>
          <div className="text-[10.5px] leading-relaxed text-slate-300">{contentSummary.length > 240 ? `${contentSummary.slice(0, 240)}...` : contentSummary}</div>
        </div>
      ) : null}
      {sceneTimeline.length ? (
        <div className="rounded-md border border-white/[0.05] bg-black/20 px-2.5 py-2">
          <div className="mb-1.5 text-[9px] uppercase tracking-wider text-slate-500">分镜时间线</div>
          <div className="space-y-1">
            {sceneTimeline.map((row) => (
              <div key={row.key} className="flex items-start gap-2 text-[10px] leading-relaxed">
                <span className="shrink-0 rounded bg-cyan-500/12 px-1.5 py-0.5 font-mono tabular-nums text-cyan-200">{row.timestamp || "—"}</span>
                <span className="text-slate-300">{row.what || "—"}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function UrlSummary({
  result,
  apiToken,
  canExecute,
  isExecuting,
  onExecute,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
  apiToken: string;
  canExecute: boolean;
  isExecuting: boolean;
  onExecute: () => void;
}) {
  const profileFlow = asRecord(result.profile_flow);
  const videoFlow = asRecord(result.video_flow);
  const creator = asRecord(result.creator_identity || videoFlow.creator_identity);
  const metadata = asRecord(result.video_metadata || videoFlow.video_metadata);
  const evidence = asRecord(videoFlow.evidence_result);
  const enqueue = asRecord(videoFlow.enqueue_result);
  const enqueueJob = asRecord(enqueue.job);
  const analysis = asRecord(videoFlow.analysis);
  const deepResult = asRecord(analysis.deep_result);
  const jobLastError = cleanText(videoFlow.job_last_error || profileFlow.job_last_error);
  const jobStatus = cleanText(videoFlow.job_status || profileFlow.job_status || videoFlow.status || profileFlow.status);
  const flowStatus = cleanText(videoFlow.status || profileFlow.status || (result.search_session ? asRecord(result.search_session).item_status : ""));
  const latency = durationLabel(analysis.latency_ms);
  const platform = cleanText(result.platform).toLowerCase();
  const isVideo = result.url_type === "video";
  const cachedVideoUrl = cleanText(videoFlow.cached_video_url);
  const youtubeVideoId = cleanText(result.video_id || videoFlow.video_id);
  const videoPoster = proxiedImageUrl(cleanText(metadata.thumbnail_url));
  const hasPlayableVideo = isVideo && (platform === "youtube" ? Boolean(youtubeVideoId) : Boolean(cachedVideoUrl));
  const profileOperation = cleanText(profileFlow.operation);
  const videoOperation = cleanText(videoFlow.operation);
  const operation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : videoOperation || profileOperation;
  const knownCreator = Boolean(result.in_pool || operation === "existing_creator_video_analysis");
  const creatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(creator.handle || creator.channel_id || creator.profile_url || result.handle || result.channel_id),
  );
  const tiktokRisk = isVideo && platform === "tiktok";
  const retryableFailure = Boolean(isVideo && jobLastError && ["failed", "blocked"].includes(jobStatus));
  const executeDone = result.execute && (
    isVideo ? videoExecutionDone(flowStatus || videoFlow.status) : cleanText(profileFlow.status) === "ready"
  );
  // profile 已改自动 execute(识别即自动抓资料入库),手动按钮只在 profile 抓取失败时作「重试」兜底。
  const profileFailed = !isVideo && ["crawl_failed", "failed"].includes(cleanText(profileFlow.status));
  const profileRetryable = profileFailed && canExecute;
  // 账号资料自动抓取中(用户贴 URL → 自动 execute,无二次确认):展示自动状态,不再显示「抓基础资料」按钮。
  const profileAutoRunning = !isVideo && isExecuting;
  const showActionButton = isVideo || profileRetryable;
  const actionLabel = isVideo
    ? retryableFailure ? "重试分析" : knownCreator ? "只分析此视频" : "建档并分析"
    : "重试抓资料";
  const disabledReason = isVideo && !creatorResolved
    ? "创作者未解析，不能建匿名档，也不会入队。"
    : result.url_type === "unknown"
      ? "无法识别 URL。"
      : "";

  return (
    <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-950/[0.10] p-3">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
            <span className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-cyan-100">
              {isVideo ? <Video size={11} /> : <BadgeCheck size={11} />}
              {urlTypeLabel(result.url_type)}
            </span>
            <span className="rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-slate-300">{display(result.platform)}</span>
            <span className="rounded-md border border-white/[0.08] bg-black/20 px-2 py-1 text-slate-300">
              {result.in_pool ? `已在库 #${display(result.matched_kol_pool_id)}` : "未命中库内 KOL"}
            </span>
            {tiktokRisk ? (
              <span className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1 text-amber-100">
                TikTok final_v1 有 media_resolve_failed 风险
              </span>
            ) : null}
          </div>
          {isVideo ? (
            <VideoCreatorCard creator={creator} metadata={metadata} />
          ) : (
            <div className="mt-2 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-2">
              <div className="truncate">对象: <span className="text-slate-200">{display(metadata.title || creator.display_name || creator.handle || result.handle || result.video_id)}</span></div>
              <div className="truncate">身份: <span className="text-slate-200">{display(creator.channel_id || creator.handle || result.channel_id || result.handle)}</span></div>
            </div>
          )}
          <div className="mt-2 truncate text-[11px] text-slate-400">normalized: <span className="text-slate-500">{display(result.url?.normalized)}</span></div>
        </div>
        <div className="shrink-0">
          {showActionButton ? (
            <button
              type="button"
              onClick={onExecute}
              disabled={!canExecute}
              className="inline-flex min-h-[34px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.14] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:border-white/[0.08] disabled:bg-white/[0.04] disabled:text-slate-500"
              title={disabledReason || "确认后执行，任务进侧边栏任务看板"}
            >
              {isExecuting ? <Loader2 size={12} className="animate-spin" /> : isVideo && !knownCreator ? <UserPlus size={12} /> : <Database size={12} />}
              {isExecuting ? "执行中..." : actionLabel}
            </button>
          ) : !isVideo ? (
            <span
              className="inline-flex min-h-[34px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.10] px-3 text-[11px] font-medium text-cyan-100"
              title="账号 URL 已自动抓取基础资料并入库，无需手动确认"
            >
              {profileAutoRunning ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />}
              {profileAutoRunning ? "自动抓资料中..." : "已自动抓资料入库"}
            </span>
          ) : null}
        </div>
      </div>
      {hasPlayableVideo ? (
        <div className="mt-2">
          <div className="mx-auto w-full max-w-2xl overflow-hidden rounded-md border border-white/[0.08] bg-black/40">
            <div className="relative w-full" style={{ aspectRatio: "16 / 9" }}>
              {platform === "youtube" ? (
                <iframe
                  src={youtubeEmbedUrl(youtubeVideoId)}
                  title={display(metadata.title || result.video_id)}
                  className="absolute inset-0 h-full w-full"
                  allow="autoplay; encrypted-media; picture-in-picture"
                  allowFullScreen
                />
              ) : (
                <video
                  src={cachedVideoUrl}
                  poster={videoPoster || undefined}
                  controls
                  playsInline
                  preload="metadata"
                  className="absolute inset-0 h-full w-full bg-black object-contain"
                />
              )}
            </div>
          </div>
          {apiToken ? (
            <VideoSceneAnalysis
              apiToken={apiToken}
              evidenceId={String(videoFlow.evidence_id ?? result.matched_kol_pool_id ?? "").trim()}
            />
          ) : null}
        </div>
      ) : null}
      {disabledReason ? (
        <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1.5 text-[10.5px] text-amber-100">
          {disabledReason}
        </div>
      ) : null}
      {!isVideo && Object.keys(asRecord(profileFlow.profile_data)).length ? (
        <ProfileInfoCard data={asRecord(profileFlow.profile_data)} />
      ) : null}
      {executeDone ? (
        <div className={`mt-2 rounded-md border px-2 py-1.5 text-[10.5px] ${
          flowStatus === "partial"
            ? "border-amber-300/20 bg-amber-400/[0.10] text-amber-100"
            : "border-emerald-300/20 bg-emerald-400/[0.10] text-emerald-100"
        }`}>
          {flowStatus === "partial" ? "部分完成" : "执行完成"}: {isVideo
            ? `kol_pool_id ${display(videoFlow.kol_pool_id || result.matched_kol_pool_id)} · evidence_id ${display(videoFlow.evidence_id || evidence.evidence_id || analysis.source_evidence_id)} · job_id ${display(enqueueJob.id || enqueue.id)}`
            : `kol_pool_id ${display(profileFlow.kol_pool_id)} · V6 Fit 未触碰 ${display(profileFlow.viltrox_fit_score_untouched)}`}
          {analysis.cache_id ? ` · cache #${display(analysis.cache_id)}` : ""}
          {deepResult.id ? ` · deep #${display(deepResult.id)}` : ""}
          {latency ? ` · 耗时 ${latency}` : ""}
        </div>
      ) : null}
      {jobLastError ? (
        <div className="mt-2 rounded-md border border-rose-300/20 bg-rose-500/[0.08] px-2 py-1.5 text-[10.5px] text-rose-100">
          分析失败: {jobLastError}
        </div>
      ) : null}
      {!result.execute && actionDescription(result.next_action) ? (
        <div className="mt-2 text-[10px] leading-relaxed text-slate-500">{actionDescription(result.next_action)}</div>
      ) : null}
    </div>
  );
}

export function SmartKolInputPanel({
  apiToken = "",
  onRecallItems,
  onOpenRecallItem,
}: {
  apiToken?: string;
  onRecallItems?: (items: VkpiKolRecallItem[]) => void;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
}) {
  const [input, setInput] = useState("");
  const [state, setState] = useState<State>("idle");
  const [mode, setMode] = useState<Mode>("idle");
  const [urlResult, setUrlResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(null);
  const [recallResult, setRecallResult] = useState<VkpiKolRecallResponse | null>(null);
  const [advanceResult, setAdvanceResult] = useState<VkpiKolSmartSearchProfileAdvanceResponse | null>(null);
  const [activeSearchSessionId, setActiveSearchSessionId] = useState<number | null>(null);
  const [activeSearchSession, setActiveSearchSession] = useState<VkpiKolSearchHistoryItem | null>(null);
  const [sessionPollNotice, setSessionPollNotice] = useState("");
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  // 问题2 平台选择器:默认全选已落地的 YT/IG/TikTok(FB 待 provider 落地,UI 置灰)。
  const [discoveryPlatforms, setDiscoveryPlatforms] = useState<string[]>(["youtube", "instagram", "tiktok"]);
  // 开闸全量(用户裁令「直接开闸全量」):深度查找默认开,文字搜索后自动触发全网发现一步到位。
  const [deepFindOn, setDeepFindOn] = useState(true);
  // P0-6 地区口径:默认开,排除 {中国大陆 CN / 香港 HK / 台湾 TW} 三地区(按 country/market 地区判据,
  // 含 ISO 码与中文地名),其余所有国家放行(含海外中文博主)。后端参数名保留 exclude_chinese。
  const [excludeChinese, setExcludeChinese] = useState(true);

  const inferredMode = useMemo(() => detectMode(input), [input]);
  const isBusy = state === "loading" || state === "executing";
  const profileFlow = asRecord(urlResult?.profile_flow);
  const videoFlow = asRecord(urlResult?.video_flow);
  const videoCreator = asRecord(urlResult?.creator_identity || videoFlow.creator_identity);
  const videoStatus = cleanText(profileFlow.status || videoFlow.status);
  const videoJobStatus = cleanText(videoFlow.job_status || videoStatus);
  const videoJobLastError = cleanText(videoFlow.job_last_error);
  const profileOperation = cleanText(profileFlow.operation);
  const rawVideoOperation = cleanText(videoFlow.operation);
  const videoOperation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : rawVideoOperation || profileOperation;
  const videoCreatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(videoCreator.handle || videoCreator.channel_id || videoCreator.profile_url || urlResult?.handle || urlResult?.channel_id),
  );
  const urlCanExecute = Boolean(
    apiToken &&
    urlResult &&
    (!urlResult.execute || Boolean(videoJobLastError)) &&
    !isBusy &&
    (
      (urlResult.url_type === "profile" && cleanText(profileFlow.status) === "dry_run_ready") ||
      (urlResult.url_type === "video" && Boolean(videoJobLastError) && ["failed", "blocked"].includes(videoJobStatus)) ||
      (
        urlResult.url_type === "video" &&
        ["dry_run_ready", "ready_to_execute"].includes(videoStatus) &&
        videoCreatorResolved &&
        ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(videoOperation)
      )
    )
  );
  const recallItems = useMemo(() => recallTopItems(recallResult), [recallResult]);
  const llmPlan = asRecord((recallResult as Row | null)?.llm_query_plan);
  // 三框·框3:全网发现项(new_creator)从在役 advance 会话抽取,与框2 库内召回分开展示。
  const discoveryItems = useMemo(() => discoveryItemsFromSession(activeSearchSession), [activeSearchSession]);
  // 三框·框1:LLM 人群理解可编辑(防 LLM 理解偏)——编辑后「用此重搜」。
  const [personaEditing, setPersonaEditing] = useState(false);
  const [personaDraft, setPersonaDraft] = useState("");
  const activeSessionCounts = sessionAdvanceCounts(activeSearchSession);
  const activeSessionSummary = asRecord(activeSearchSession?.result_summary);
  const activeSmartJob = asRecord(activeSessionSummary.smart_search_profile_advance_job);
  const activeSessionStatus = cleanText(activeSmartJob.advance_status || activeSmartJob.status || activeSearchSession?.status);

  useEffect(() => {
    if (recallItems.length) onRecallItems?.(recallItems);
  }, [recallItems, onRecallItems]);

  const refreshHistory = useCallback(async () => {
    if (!apiToken) {
      setHistoryItems([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const response = await listKolSearchHistory(apiToken, { limit: 10, itemLimit: 5 });
      setHistoryItems(Array.isArray(response.items) ? response.items : []);
    } catch {
      // History is a convenience surface; do not interrupt the main search flow.
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken]);

  const restoreSession = useCallback((session: VkpiKolSearchHistoryItem) => {
    const query = cleanText(session.query_text);
    if (query) setInput(query);
    setAdvanceResult(null);
    setActiveSearchSessionId(null);
    setActiveSearchSession(session);
    setSessionPollNotice("");
    setError("");
    const queryType = cleanText(session.query_type);
    if (queryType === "url_video" || queryType === "url_profile") {
      const nextUrlResult = urlResultFromSession(session);
      setMode("url");
      setUrlResult(nextUrlResult);
      setRecallResult(null);
    } else {
      setMode("text");
      setRecallResult(recallResultFromSession(session));
      setUrlResult(null);
    }
    setState("ready");
  }, []);

  const applyPolledSession = useCallback((session: VkpiKolSearchHistoryItem) => {
    setActiveSearchSession(session);
    const queryType = cleanText(session.query_type);
    if (queryType === "url_video" || queryType === "url_profile") {
      setMode("url");
      setUrlResult(urlResultFromSession(session));
      setRecallResult(null);
      return;
    }
    setMode("text");
    setRecallResult(recallResultFromSession(session));
    setUrlResult(null);
  }, []);

  const openHistorySession = useCallback(async (sessionOrId: VkpiKolSearchHistoryItem | number | string) => {
    if (!apiToken) return;
    const knownSession = typeof sessionOrId === "object" ? sessionOrId : null;
    const sessionId = knownSession ? historySessionId(knownSession) : Number(sessionOrId);
    if (!sessionId) {
      if (knownSession) restoreSession(knownSession);
      return;
    }
    setHistoryLoading(true);
    try {
      const session = await getKolSearchSession(apiToken, sessionId);
      restoreSession(session);
      void refreshHistory();
    } catch (err) {
      if (knownSession) {
        restoreSession(knownSession);
      } else {
        setError(err instanceof Error ? err.message : "历史记录读取失败");
      }
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken, refreshHistory, restoreSession]);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    if (!apiToken || typeof window === "undefined") return undefined;
    const openPending = (sessionId: unknown) => {
      const parsed = Number(sessionId);
      if (Number.isFinite(parsed) && parsed > 0) void openHistorySession(parsed);
    };
    const fromStorage = window.localStorage.getItem(PENDING_SEARCH_SESSION_KEY);
    if (fromStorage) {
      window.localStorage.removeItem(PENDING_SEARCH_SESSION_KEY);
      openPending(fromStorage);
    }
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      openPending(detail.sessionId || detail.session_id);
    };
    window.addEventListener("vkpi:open-kol-search-session", handler);
    return () => window.removeEventListener("vkpi:open-kol-search-session", handler);
  }, [apiToken, openHistorySession]);

  useEffect(() => {
    if (!apiToken || !activeSearchSessionId || typeof window === "undefined") return undefined;
    let cancelled = false;
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;
    const poll = async () => {
      try {
        const session = await getKolSearchSession(apiToken, activeSearchSessionId);
        if (cancelled) return;
        applyPolledSession(session);
        if (isSearchSessionTerminal(session)) {
          setActiveSearchSessionId(null);
          setSessionPollNotice("后台深度查找已回填");
          void refreshHistory();
          return;
        }
        if (Date.now() - startedAt > maxPollMs) {
          setActiveSearchSessionId(null);
          setSessionPollNotice("后台深度查找仍在运行，可从最近历史或侧边栏任务继续打开");
          void refreshHistory();
        }
      } catch (err) {
        if (cancelled) return;
        setSessionPollNotice(err instanceof Error ? err.message : "后台深度查找同步失败，下次轮询会重试");
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSearchSessionId, apiToken, applyPolledSession, refreshHistory]);

  const run = async (overrideQuery?: string) => {
    const query = cleanText(overrideQuery ?? input);
    if (!apiToken) {
      setState("error");
      setError("未登录 / 无 token");
      return;
    }
    if (!query) {
      setState("error");
      setError("输入为空");
      return;
    }
    const nextMode = detectMode(query);
    setMode(nextMode);
    setState("loading");
    setError("");
    setUrlResult(null);
    setRecallResult(null);
    setAdvanceResult(null);
    setActiveSearchSessionId(null);
    setActiveSearchSession(null);
    setSessionPollNotice("");
    try {
      const response = await smartKolSearch(apiToken, query, {
        mode: "auto",
        maxPosts: 3,
        candidateLimit: 50,
        limit: 10,
        creatorQuota: 7,
        reviewerQuota: 3,
        createSession: true,
        excludeChinese,
        timeoutMs: 60000,
      });
      const responseMode = cleanText(response.mode);
      const isText = !(responseMode === "url" || cleanText(response.query_type).startsWith("url_"));
      let autoProfile: VkpiKolUrlDeepCrawlResponse | null = null;
      if (!isText) {
        setMode("url");
        const urlPayload = response.result as VkpiKolUrlDeepCrawlResponse;
        setUrlResult(urlPayload);
        // 账号 URL 自动入库:识别为 profile 且后端 dry-run 就绪(dry_run_ready)→ 直接自动 execute
        // (mode profile_basics:只抓基础资料 + 入库,不触发昂贵视频深析),前端随后展示 ProfileInfoCard。
        // video URL 不自动(视频分析更重,保留手动确认)。
        if (
          urlPayload.url_type === "profile" &&
          !urlPayload.execute &&
          cleanText(asRecord(urlPayload.profile_flow).status) === "dry_run_ready"
        ) {
          autoProfile = urlPayload;
        }
      } else {
        setMode("text");
        setRecallResult(response.result as VkpiKolRecallResponse);
      }
      setState("ready");
      void refreshHistory();
      // 开闸全量:文字搜索且深度查找开关开 → 自动触发全网发现(advance-job 全量,含所选平台),
      // 一步「先库内召回 → 再全网发现」,不必再手点。护栏 enforce 兜底超支。
      if (isText && deepFindOn) void queueTextAdvance(overrideQuery);
      // 账号 URL 自动抓资料 + 入库(不再弹「抓基础资料」二次确认)。
      if (autoProfile) void runUrlExecute(autoProfile, { auto: true });
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "智能入口请求失败");
    }
  };

  // URL 执行核心:source 显式传当次结果(避免 setUrlResult 后读到旧 state),auto=true 为 profile 自动跑。
  // profile 用 mode "profile_basics"(非 "auto")——只抓基础资料 + 入库,绝不触发 representative_video
  // final_v1 视频深析(后端 _profile_should_enqueue_representative_videos 仅认 auto/profile_with_video/
  // account_deep);video 仍走 video_deep + 手动确认。V6 Fit 由 write_kol_profile_basics 白名单兜底不触碰。
  const runUrlExecute = async (source: VkpiKolUrlDeepCrawlResponse, opts: { auto?: boolean } = {}) => {
    const query = cleanText(source.url?.input || input);
    if (!apiToken || !query) return;
    const sourceProfileFlow = asRecord(source.profile_flow);
    setState("executing");
    setError("");
    try {
      const executeMode = source.url_type === "video" ? "video_deep" : "profile_basics";
      const sessionId = sessionIdFrom(source.search_session);
      const response = await deepCrawlKolUrl(apiToken, query, true, {
        maxPosts: typeof sourceProfileFlow.max_posts === "number" ? sourceProfileFlow.max_posts : 3,
        mode: executeMode,
        sessionId,
        createSession: !sessionId,
        source: opts.auto ? "smart_kol_input_auto" : "smart_kol_input",
        timeoutMs: 300000,
      });
      setUrlResult(response);
      const nextSessionId = sessionIdFrom(response.search_session) || sessionId;
      if (nextSessionId) {
        setActiveSearchSessionId(nextSessionId);
        setSessionPollNotice(response.url_type === "video" ? "视频分析状态同步中..." : "账号资料抓取状态同步中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "URL 执行失败");
    }
  };

  // 手动执行(视频区「只分析此视频」/「建档并分析」按钮 + profile 重试兜底):沿用受控 canExecute 门槛。
  const executeUrlAction = async () => {
    if (!urlResult || !urlCanExecute) return;
    await runUrlExecute(urlResult);
  };

  const queueTextAdvance = async (overrideQuery?: string) => {
    const query = cleanText(overrideQuery ?? input);
    if (!apiToken || !query || state === "executing") return;
    setState("executing");
    setError("");
    try {
      const response = await smartKolSearchProfileAdvanceJob(apiToken, query, {
        candidateLimit: 100,
        limit: 30,
        creatorQuota: 15,
        reviewerQuota: 15,
        advanceLimit: 15,
        maxPosts: 12,
        representativeVideoLimit: 1,
        includeNewDiscovery: true,
        newDiscoveryLimit: 15,
        newDiscoveryPlatforms: discoveryPlatforms,
        excludeChinese,
        timeoutMs: 300000,
      });
      setAdvanceResult(response);
      const queuedSession = response.search_session && typeof response.search_session === "object"
        ? response.search_session as VkpiKolSearchHistoryItem
        : null;
      const sessionId = sessionIdFrom(response.search_session) || sessionIdFrom(response.advance_job) || sessionIdFrom(queuedSession);
      if (queuedSession && sessionItems(queuedSession).length) applyPolledSession(queuedSession);
      if (sessionId) {
        setActiveSearchSessionId(sessionId);
        setSessionPollNotice("后台深度查找同步中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "后台深度查找入队失败");
    }
  };

  return (
    <section
      data-testid="smart-kol-input-panel"
      className="rounded-lg border border-white/[0.065] bg-black/[0.14] p-2.5"
    >
      <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-cyan-300/15 bg-cyan-400/[0.08] text-cyan-100">
            <Sparkles size={12} />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <h2 className="text-[12px] font-semibold text-white">统一智能入口</h2>
              <span className="rounded-full border border-emerald-300/12 bg-emerald-400/[0.05] px-1.5 py-0.5 text-[9px] text-emerald-100">
                V6 Fit 不触碰
              </span>
            </div>
            <div className="mt-0.5 truncate text-[10px] text-slate-600">
              URL 自动分流；文字走查找。
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[9px] text-slate-500">
          <span className="rounded border border-cyan-300/10 bg-cyan-400/[0.035] px-1.5 py-0.5 text-cyan-100">Video</span>
          <span className="rounded border border-violet-300/10 bg-violet-400/[0.035] px-1.5 py-0.5 text-violet-100">Profile</span>
          <span className="rounded border border-emerald-300/10 bg-emerald-400/[0.035] px-1.5 py-0.5 text-emerald-100">查找</span>
        </div>
      </div>

      <form
        className="mt-2 grid gap-2 lg:grid-cols-[minmax(0,1fr)_112px]"
        onSubmit={(event) => {
          event.preventDefault();
          if (isBusy || !apiToken || !cleanText(input)) return;
          void run();
        }}
      >
        <input
          data-testid="smart-kol-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !isBusy) void run();
          }}
          placeholder="粘贴 KOL 主页 / 视频 URL，或输入产品需求，例如: 35mm 低光人像 YouTube 摄影师"
          className="min-h-[40px] rounded-md border border-white/[0.075] bg-black/30 px-3 py-2 text-[11.5px] text-slate-200 outline-none placeholder-slate-600 focus:border-cyan-300/45"
        />
        <button
          data-testid="smart-kol-run"
          type="submit"
          disabled={isBusy || !apiToken || !cleanText(input)}
          className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/18 bg-cyan-500/[0.14] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {isBusy ? <Loader2 size={13} className="animate-spin" /> : inferredMode === "url" ? <Link2 size={13} /> : <Search size={13} />}
          {inferredMode === "url" ? "识别 URL" : "查找"}
        </button>
      </form>

      {state === "idle" && !input ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9.5px] text-slate-600">
          <span className="inline-flex items-center gap-1 text-cyan-100"><Video size={9} /> 视频 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-violet-100"><BadgeCheck size={9} /> 账号 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-emerald-100"><Search size={9} /> 产品需求</span>
          <span>先识别，再执行。</span>
        </div>
      ) : null}

      <HistoryStrip
        items={historyItems}
        loading={historyLoading}
        onOpen={(session) => void openHistorySession(session)}
      />

      {error ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-200">{error}</div>
      ) : null}

      {mode === "url" && urlResult ? (
        <UrlSummary
          result={urlResult}
          apiToken={apiToken}
          canExecute={urlCanExecute}
          isExecuting={state === "executing"}
          onExecute={() => void executeUrlAction()}
        />
      ) : null}

      {mode === "text" && recallResult ? (
        <div className="mt-3 space-y-2.5">
          {/* 框1 · 产品人群分析(可编辑,防 LLM 理解偏) */}
          <div className="rounded-lg border border-cyan-300/15 bg-cyan-400/[0.04] p-3">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="text-[11px] font-medium text-cyan-100">① 产品人群分析 · 先识人群再找人</div>
              {!personaEditing ? (
                <button
                  type="button"
                  onClick={() => { setPersonaDraft(display(llmPlan.search_query, cleanText(input))); setPersonaEditing(true); }}
                  className="rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-white/[0.2] hover:text-white"
                >编辑</button>
              ) : null}
            </div>
            {personaEditing ? (
              <div className="space-y-1.5">
                <textarea
                  value={personaDraft}
                  onChange={(event) => setPersonaDraft(event.target.value)}
                  rows={2}
                  className="w-full resize-none rounded-md border border-white/[0.1] bg-black/30 px-2 py-1.5 text-[11px] text-white placeholder-slate-600 focus:border-cyan-400/40 focus:outline-none"
                  placeholder="改写「要找什么样的人」让 LLM 更懂你的产品需求…"
                />
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={!cleanText(personaDraft) || isBusy}
                    onClick={() => { const q = cleanText(personaDraft); setInput(q); setPersonaEditing(false); void run(q); }}
                    className="rounded-md border border-cyan-300/25 bg-cyan-500/[0.14] px-2.5 py-1 text-[10px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.22] disabled:cursor-not-allowed disabled:opacity-50"
                  >用此重搜</button>
                  <button type="button" onClick={() => setPersonaEditing(false)} className="text-[10px] text-slate-500 hover:text-slate-300">取消</button>
                </div>
              </div>
            ) : Object.keys(llmPlan).length ? (
              <PlanPills plan={llmPlan} />
            ) : (
              <div className="text-[10px] text-slate-500">本次走规则解析(无 LLM 计划);点「编辑」改写查询后「用此重搜」。</div>
            )}
          </div>

          {/* 框2 · 库内账号匹配 */}
          <div className="rounded-lg border border-violet-300/15 bg-violet-950/[0.10] p-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] font-medium text-violet-100">② 库内账号匹配 · 命中 {display(recallResult.diagnostics?.candidate_count)} 条</div>
              <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-500">
                <span className="rounded-md border border-white/[0.07] px-2 py-1">创作者 {display(recallResult.diagnostics?.creator_returned)}</span>
                <span className="rounded-md border border-white/[0.07] px-2 py-1">测评号 {display(recallResult.diagnostics?.reviewer_returned)}</span>
              </div>
            </div>
            {recallItems.length ? (
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {recallItems.map((item, index) => (
                  <RecallMiniItem key={`r-${item.bucket}-${item.kol_pool_id || item.handle || index}`} item={item} index={index + 1} onOpen={onOpenRecallItem} />
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">暂无库内匹配</div>
            )}
          </div>

          {/* 框3 · 全网发现(Apify+平台,带头像) */}
          <div className="rounded-lg border border-emerald-300/15 bg-emerald-950/[0.10] p-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] font-medium text-emerald-100">③ 全网发现 · Apify+平台{discoveryItems.length ? ` · ${discoveryItems.length} 个` : ""}</div>
              <label className="flex items-center gap-1 text-[10px] text-slate-400">
                <input type="checkbox" checked={deepFindOn} onChange={(event) => setDeepFindOn(event.target.checked)} className="accent-emerald-500" />
                默认开
              </label>
            </div>
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] text-slate-500">发现平台</span>
              {[{ k: "youtube", t: "YouTube" }, { k: "instagram", t: "Instagram" }, { k: "tiktok", t: "TikTok" }].map((p) => {
                const on = discoveryPlatforms.includes(p.k);
                return (
                  <button
                    key={p.k}
                    type="button"
                    onClick={() => setDiscoveryPlatforms((cur) => (on ? cur.filter((x) => x !== p.k) : [...cur, p.k]))}
                    className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${on ? "border-cyan-300/40 bg-cyan-400/[0.12] text-cyan-100" : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"}`}
                  >{p.t}</button>
                );
              })}
              <span className="rounded-full border border-white/[0.06] px-2 py-0.5 text-[10px] text-slate-600" title="Facebook 发现待 provider 落地">Facebook · 即将</span>
              <label className="flex items-center gap-1 text-[10px] text-slate-400" title="排除 中国大陆/香港/台湾 地区(按 country/market 地区判据,海外中文博主放行)">
                <input type="checkbox" checked={excludeChinese} onChange={(event) => setExcludeChinese(event.target.checked)} className="accent-emerald-500" />
                排除 中国/港/台 地区
              </label>
              <button
                type="button"
                onClick={() => void queueTextAdvance()}
                disabled={state === "executing" || !apiToken || !cleanText(input)}
                className="ml-auto inline-flex min-h-[28px] items-center justify-center gap-1.5 rounded-md border border-emerald-300/18 bg-emerald-500/[0.12] px-2.5 text-[10px] font-medium text-emerald-100 transition-colors hover:bg-emerald-500/[0.20] disabled:cursor-not-allowed disabled:opacity-55"
              >
                {state === "executing" ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                {deepFindOn ? "重新全网查找" : "立即全网查找"}
              </button>
            </div>
            {discoveryItems.length ? (
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {discoveryItems.map((item, index) => (
                  <RecallMiniItem key={`d-${item.kol_pool_id || item.handle || index}`} item={item} index={index + 1} onOpen={onOpenRecallItem} />
                ))}
              </div>
            ) : activeSearchSessionId ? (
              <div className="flex items-center gap-1.5 rounded-md border border-emerald-300/15 bg-black/15 px-2.5 py-2 text-[10.5px] text-emerald-100/80">
                <Loader2 size={12} className="animate-spin" /> 全网发现进行中…后台按所选平台抓取,完成自动回填{activeSearchSession?.id ? ` · session #${activeSearchSession.id}` : ""}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-3 text-center text-[10.5px] text-slate-500">{deepFindOn ? "深度查找默认开 · 搜索后自动从所选平台发现新号" : "点「立即全网查找」从所选平台发现新号"}</div>
            )}
            {advanceResult || activeSearchSession || sessionPollNotice ? (
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-emerald-100/65">
                <span>{display(activeSessionStatus || advanceResult?.status, "queued")}</span>
                {Object.keys(activeSessionCounts).length ? (
                  <>
                    <span className="rounded border border-emerald-300/15 bg-black/15 px-1.5 py-0.5">ready {display(activeSessionCounts.ready, "0")}</span>
                    <span className="rounded border border-emerald-300/15 bg-black/15 px-1.5 py-0.5">executed {display(activeSessionCounts.executed, "0")}</span>
                    <span className="rounded border border-emerald-300/15 bg-black/15 px-1.5 py-0.5">errors {display(activeSessionCounts.errors, "0")}</span>
                  </>
                ) : null}
                {sessionPollNotice ? <span className="text-emerald-200/70">{sessionPollNotice}</span> : null}
                <span className="ml-auto">V6 Fit 未触碰: {display(advanceResult?.viltrox_fit_score_untouched ?? activeSmartJob.viltrox_fit_score_untouched, "true")}</span>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

    </section>
  );
}
