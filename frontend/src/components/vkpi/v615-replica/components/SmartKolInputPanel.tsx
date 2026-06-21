import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, BadgeCheck, Clock3, Database, Info, Link2, Loader2, Search, ShieldCheck, Sparkles, TrendingUp, UserPlus, Video } from "lucide-react";

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
import { enqueueAllKolVideos, getKolVideoAnalysisCache, translateBio, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";
// A1·复用 KOLVideoAnalysisPanel 的画面质量分 / 三观-归因-建议 / 关键帧 QA 渲染原子(纯读 final_v1/QA 缓存,绝不触 viltrox_fit_score)。
import {
  DeepLayersSection,
  analysisScoreColor,
  compactText,
  finalV1QaPayload,
  firstText,
  normaliseScore,
  qaBoolean,
  qaCheckTags,
  qaIssueItems,
  qaScoreCorrectionText,
  qaStatusClass,
  qaStatusLabel,
  textFrom,
} from "./KOLVideoAnalysisPanel";

type Mode = "idle" | "url" | "text";
type State = "idle" | "loading" | "ready" | "executing" | "error";
type Row = Record<string, unknown>;
const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";
// 刀2·流2 路A:贴账号 URL 自动分析的代表视频条数(dossier 据此出 LLM 账号分)。2 = 信号与成本/排队的折中;
// 想更深可在抽屉点「发现并分析全部视频」。代表视频走交互优先(并发A tier0)插队,不被批量饿死。
const PROFILE_REP_VIDEO_LIMIT = 2;
// 搜索展示态持久化:把当前激活搜索的 ①②③ 显示态存进 sessionStorage,挂载时回填。
// 即便父级 90s/10min 刷新偶发重挂本面板(useState 归零),也能恢复结果,不让用户的搜索凭空消失。
const ACTIVE_SEARCH_DISPLAY_KEY = "vkpi:activeKolSearchDisplay";

function cleanText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

// 搜索展示态的持久化形状(只存渲染所需,够回填 ①②③ 与轮询续接)。
type PersistedSearchDisplay = {
  input: string;
  mode: Mode;
  recallResult: VkpiKolRecallResponse | null;
  urlResult: VkpiKolUrlDeepCrawlResponse | null;
  activeSearchSession: VkpiKolSearchHistoryItem | null;
  activeSearchSessionId: number | null;
};

function readPersistedSearchDisplay(): PersistedSearchDisplay | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_SEARCH_DISPLAY_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedSearchDisplay;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function writePersistedSearchDisplay(value: PersistedSearchDisplay | null): void {
  if (typeof window === "undefined") return;
  try {
    if (!value) {
      window.sessionStorage.removeItem(ACTIVE_SEARCH_DISPLAY_KEY);
      return;
    }
    window.sessionStorage.setItem(ACTIVE_SEARCH_DISPLAY_KEY, JSON.stringify(value));
  } catch {
    // 配额/隐私模式失败时静默:持久化只是兜底,失败不影响实时搜索。
  }
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
      display_rank_score: Number(payload.display_rank_score ?? item.score ?? payload.recall_rank_score ?? 0),
      relevance_flags: Array.isArray(payload.relevance_flags) ? (payload.relevance_flags as unknown[]).map(cleanText).filter(Boolean) : [],
      relevance_tier_hint: cleanText(payload.relevance_tier_hint),
      type_label: bucket === "reviewer" ? "测评号" : "创作者",
      creator_type_score: bucket === "creator" ? 1 : 0,
      reviewer_type_score: bucket === "reviewer" ? 1 : 0,
      recall_reason: cleanText(payload.evidence || payload.sample_title),
      // why_fit:实时/历史会话项透传(payload.why_fit 由后端 attach_recall_result 写入;缺则回退召回理由)。
      why_fit: cleanText(payload.why_fit || payload.evidence),
      // 三引擎展示信号透传(纯只读;后端在会话项 payload 写入则亮起,否则静默不渲染)。
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? 0) || null,
      source_fields: payload,
    } as VkpiKolRecallItem;
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

// 滤掉零售/经销实体(B&H / Pro Audio / camera store 等)——不是真创作者,挂出来没意义。
function looksLikeRetailer(item: any): boolean {
  const sf = (item && item.source_fields && typeof item.source_fields === "object") ? item.source_fields : {};
  const hay = `${item?.handle || ""} ${item?.display_name || ""} ${item?.why_fit || ""} ${sf.bio || sf.description || ""}`.toLowerCase();
  return /\bb\s*&\s*h\b|b\s*and\s*h|pro\s*audio|photo\s*video|camera\s*(store|house|shop|world|land)|rental|retailer|wholesale|distributor|旗舰店|专卖|经销/.test(hay);
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
      followers: Number(payload.followers || payload.follower_count || payload.subscriber_count || payload.subscribers || payload.avg_views || payload.views || 0) || null,
      avatar_url: cleanText(payload.avatar_url),
      profile_url: cleanText(item.source_url || payload.profile_url || payload.source_url || payload.channel_url),
      recall_rank_score: Number(item.score ?? payload.score ?? 0),
      vector_score: Number(payload.vector_score ?? item.score ?? 0),
      type_label: "全网发现",
      creator_type_score: 1,
      reviewer_type_score: 0,
      recall_reason: cleanText(payload.sample_title || payload.evidence),
      why_fit: cleanText(payload.why_fit || payload.sample_title),
      // 三引擎展示信号(发现项:exposure 用 avg_views/views;published/historical_match 透 source_fields 供新人/新鲜判定)。
      fit_verdict: cleanText(payload.fit_verdict),
      creator_type: cleanText(payload.creator_type),
      exposure_potential: Number(payload.exposure_potential ?? payload.avg_views ?? payload.views ?? 0) || null,
      source_fields: payload,
    } as VkpiKolRecallItem);
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

// 全网发现状态码 → 人话(面向营销人,不暴露 queued/running 等内部状态码)。
function advanceStatusLabel(value: unknown): string {
  const status = cleanText(value).toLowerCase();
  if (["ready", "done", "partial"].includes(status)) return "已完成";
  if (["failed", "blocked"].includes(status)) return "未完成";
  if (status === "running") return "查找中";
  return "排队中";
}

// 异步会话状态横幅(诚实显示 排队/查找中/已完成/部分完成/未完成):只读后端真有的字段——
// 会话级 status(planned/running/ready/partial/failed/cancelled)+ result_summary
// .smart_search_profile_advance_job 的 {status, advance_status, advance_counts, error}。
// failed 时把后端 error 原文(异常串)收进 note,而非吞掉看似空白卡死。后端未单独返回「AI 规划
// 失败已退基础检索」原因字段(planner 失败被静默兜底),故只能据真实 status/error 说明,不编造。
type SessionBanner = { tone: "info" | "ok" | "warn" | "error"; label: string; note: string } | null;

function sessionStatusBanner(
  session: VkpiKolSearchHistoryItem | null,
  advanceStatus: string,
  counts: Row,
  polling: boolean,
): SessionBanner {
  if (!session && !advanceStatus && !polling) return null;
  const summary = asRecord(session?.result_summary);
  const job = asRecord(summary.smart_search_profile_advance_job);
  const raw = cleanText(advanceStatus || job.advance_status || job.status || session?.status).toLowerCase();
  const jobError = cleanText(job.error);
  const ready = Number(counts.ready ?? 0);
  const failed = Number(counts.failed ?? 0) + Number(counts.errors ?? 0);
  if (["failed", "blocked"].includes(raw) && ready <= 0) {
    return { tone: "error", label: "这次没找到结果", note: jobError ? `失败原因:${jobError}` : "查找未能完成,可调整描述或换个区域重试。" };
  }
  if (["partial"].includes(raw) || (failed > 0 && ready > 0)) {
    return {
      tone: "warn",
      label: "已找到部分结果",
      note: failed > 0
        ? `下方结果可直接查看;另有 ${failed} 个没跑完,可稍后重试补齐。`
        : "下方结果可直接查看;部分人选还在补全,完成后会自动更新。",
    };
  }
  if (["ready", "done"].includes(raw)) {
    return { tone: "ok", label: "已找完", note: ready > 0 ? `共找到 ${ready} 个人选,见下方。` : "这次没有新的人选,可换个描述再试。" };
  }
  if (raw === "running" || polling) {
    return { tone: "info", label: "正在查找", note: "后台正从所选平台找人,找到的会随时显示,无需等待。" };
  }
  return { tone: "info", label: "已排队", note: "已进入后台查找队列,稍候会自动开始。" };
}

function historyKindLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  if (type === "url_video") return "视频 URL";
  if (type === "url_profile") return "账号 URL";
  if (type === "text_recall") return "查找";
  return "历史";
}

// 历史标签可读化:文字搜索显查询语;URL 搜索把裸链接解析成「平台 @handle / 平台 帖id」,
// 让一堆长得一样的 instagram.com/p/... 能区分、看懂搜的是谁/什么(用户:历史要备注搜索信息)。
const _RESERVED_PATH = new Set(["p", "reel", "reels", "shorts", "video", "watch", "videos"]);

function historyLabel(session: VkpiKolSearchHistoryItem): string {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  // 数据里很多是 query_type='unknown' 且 query_text 形如「账号分析 · https://…」——
  // 去掉中文前缀、从文本里抠出 URL 再解析,而不是死认 query_type。
  const stripped = raw.replace(/^[一-龥A-Za-z]+\s*·\s*/, "").trim();
  const urlMatch = stripped.match(/https?:\/\/\S+/) || raw.match(/https?:\/\/\S+/);
  // 「账号分析」意图优先于路径启发式(如 /@x/shorts 是主页 Shorts 栏,不是单条视频)
  const isAccount = type === "url_profile" || /账号分析/.test(raw);
  const looksVideo = !isAccount && (type === "url_video" || /视频分析/.test(raw) || /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw));
  if (!urlMatch) return display(raw, "未命名"); // 纯文字搜索:原文
  try {
    const u = new URL(urlMatch[0]);
    const host = u.hostname.replace(/^www\./, "");
    const plat = host.includes("instagram")
      ? "IG"
      : host.includes("tiktok")
        ? "TikTok"
        : host.includes("youtu")
          ? "YouTube"
          : (host.split(".")[0] || "URL");
    const parts = u.pathname.split("/").filter(Boolean);
    const handleSeg = parts.find((p) => p.startsWith("@"));
    const firstNamed = parts[0] && !_RESERVED_PATH.has(parts[0]) ? parts[0] : "";
    // 账号/主页:有 @handle 或首段是用户名 → 「平台 @handle」
    if (!looksVideo && (handleSeg || firstNamed)) {
      return `${plat} @${(handleSeg || firstNamed).replace(/^@/, "")}`;
    }
    // 视频/帖:youtube 取 v 参数,其余取末段 id
    const vid = u.searchParams.get("v");
    const id = vid || parts[parts.length - 1] || firstNamed || "";
    return `${plat} ${looksVideo ? "视频" : "帖"} ${id}`.trim();
  } catch {
    return display(raw, "未命名");
  }
}

function relativeTime(value: unknown): string {
  const s = cleanText(value);
  if (!s) return "";
  const t = Date.parse(s);
  if (!Number.isFinite(t)) return "";
  const diff = Date.now() - t;
  if (diff < 60000) return "刚刚";
  const m = Math.floor(diff / 60000);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return `${Math.floor(h / 24)} 天前`;
}

function historyKindMeta(session: VkpiKolSearchHistoryItem): { label: string; cls: string } {
  const type = cleanText(session.query_type);
  const raw = cleanText(session.query_text);
  const video = { label: "视频", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100/90" };
  const account = { label: "账号", cls: "border-violet-300/30 bg-violet-400/[0.10] text-violet-100/90" };
  if (type === "url_video" || /视频分析/.test(raw)) return video;
  if (type === "url_profile" || /账号分析/.test(raw)) return account;
  if (type === "text_recall") return { label: "找人", cls: "border-cyan-300/30 bg-cyan-400/[0.10] text-cyan-100/90" };
  // query_type='unknown' 但文本含 URL:按视频路径/否则账号 兜底归类
  if (/https?:\/\//.test(raw)) return /\/(reel|reels|shorts|watch)\b|[?&]v=/.test(raw) ? video : account;
  return { label: "历史", cls: "border-white/[0.1] bg-white/[0.03] text-slate-300" };
}

function historyStatusMeta(value: unknown): { label: string; cls: string; dot: string } {
  const label = advanceStatusLabel(value);
  if (label === "已完成") return { label, cls: "text-emerald-300/85", dot: "#34d399" };
  if (label === "查找中") return { label, cls: "text-amber-300/85", dot: "#fbbf24" };
  if (label === "未完成") return { label, cls: "text-rose-300/85", dot: "#fb7185" };
  return { label, cls: "text-slate-500", dot: "#64748b" };
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
  const [expanded, setExpanded] = useState(false);
  if (!items.length && !loading) return null;
  // 收缩态:只显示「最近历史」标题(不渲染记录);展开才出列表。
  const shown = expanded ? items : [];
  return (
    <div className="mt-2 rounded-lg border border-white/[0.055] bg-black/15 px-2.5 py-2">
      <div className={`flex items-center justify-between gap-2${expanded ? " mb-1.5" : ""}`}>
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="inline-flex items-center gap-1.5 text-[10px] font-medium text-slate-300 hover:text-cyan-100"
        >
          <Clock3 size={11} className="text-slate-500" />
          最近历史
          {items.length ? <span className="text-[9px] text-slate-600">· {items.length}</span> : null}
        </button>
        <div className="inline-flex items-center gap-2">
          {loading ? <span className="text-[9.5px] text-slate-600">同步中</span> : null}
          {items.length ? (
            <button
              type="button"
              onClick={() => setExpanded((x) => !x)}
              className="text-[9.5px] font-medium text-slate-500 hover:text-cyan-200"
            >
              {expanded ? "收起" : `展开 ${items.length} 条`}
            </button>
          ) : null}
        </div>
      </div>
      <div className={expanded ? "space-y-1" : ""}>
        {shown.map((item) => {
          const sessionId = historySessionId(item);
          const label = historyLabel(item);
          const kind = historyKindMeta(item);
          const st = historyStatusMeta(item.status || "ready");
          const when = relativeTime(item.updated_at || item.created_at);
          return (
            <button
              key={`${sessionId || label}-${item.updated_at || item.created_at || ""}`}
              type="button"
              onClick={() => onOpen(item)}
              className="group flex w-full items-center gap-2 rounded-md border border-white/[0.05] bg-white/[0.015] px-2 py-1.5 text-left transition-colors hover:border-cyan-300/25 hover:bg-cyan-400/[0.04]"
              title={`${kind.label} · ${label} · ${st.label}`}
            >
              <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[8.5px] font-semibold ${kind.cls}`}>{kind.label}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-slate-300 group-hover:text-cyan-100">{label}</span>
              {when ? <span className="shrink-0 text-[9px] text-slate-600">{when}</span> : null}
              <span className={`inline-flex shrink-0 items-center gap-1 text-[9.5px] font-medium ${st.cls}`}>
                <span className="h-1 w-1 rounded-full" style={{ background: st.dot }} />
                {st.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// 问题5 UI:相关度按名次分档(列表已按分排序,名次=相关度强弱),裸 score 进 title 供细看,
// 避免向量分都 <0.5 时全显「相关」无区分。
// demote:后端 relevance_tier_hint==="demote"(如视频向产品×纯平面摄影候选)时,封顶为「中相关」,
// 绝不显「高相关」——纯展示分档,不动召回侧排序与任何评分字段。
function relevanceTier(score: number, demote = false): { label: string; cls: string; dot: string } {
  // 按真实相关度分值(0-1)分档:高≥0.6 / 中≥0.3 / 相关。demote 封顶「中相关」绝不显「高相关」。纯展示。
  if (score >= 0.6 && !demote) return { label: "高相关", cls: "border-emerald-300/35 bg-emerald-400/[0.10] text-emerald-100", dot: "#34d399" };
  if (score >= 0.3) return { label: "中相关", cls: "border-cyan-300/30 bg-cyan-400/[0.07] text-cyan-100", dot: "#22d3ee" };
  return { label: "相关", cls: "border-white/[0.08] bg-white/[0.02] text-slate-400", dot: "#64748b" };
}

// 内容契合判定 → 徽章(纯展示信号,读会话项透传的 content_fit;绝不并入/改写 viltrox_fit_score）。
// 已深析才显;verdict ∈ {fit/partial_fit/not_fit} 映射 适合/一般/不适合,不可识别则不渲染。
function contentFitBadge(value: unknown): { label: string; cls: string } | null {
  const verdict = cleanText(value).toLowerCase();
  if (verdict === "fit") return { label: "适合", cls: "border-emerald-300/35 bg-emerald-400/[0.12] text-emerald-100" };
  if (verdict === "not_fit") return { label: "不适合", cls: "border-rose-300/30 bg-rose-400/[0.10] text-rose-100" };
  if (verdict === "partial_fit") return { label: "一般", cls: "border-amber-300/30 bg-amber-400/[0.10] text-amber-100" };
  return null;
}

// 预估曝光(说人话):读会话项 exposure_potential / avg_views / views,折成 K/M 量级。
// 纯展示触达潜力(终极=提升曝光/市场),不参与任何评分。
function exposureLabel(item: VkpiKolRecallItem): string {
  const self = item as unknown as Row;
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const raw = Number(
    self.exposure_potential ?? src.exposure_potential ?? src.avg_views ?? src.views ?? 0,
  );
  return numberLabel(raw);
}

// 新人/新鲜标(纯展示,优先新人裁令):
//  - newcomer:全网发现 new_creator(无 kol_pool_id 未入库)= 优先新人主源。
//  - fresh:近 90 天有新作(published 时间戳)。
//  - lowCollab:历史无合作记录(无 historical_match / cooperation 计数为 0)。
function freshnessMarks(item: VkpiKolRecallItem): { newcomer: boolean; fresh: boolean; lowCollab: boolean } {
  const src = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const newcomer = !Number(item.kol_pool_id) && cleanText(item.type_label) === "全网发现";
  let fresh = false;
  const published = cleanText(src.published);
  if (published) {
    const ts = Date.parse(published);
    if (Number.isFinite(ts)) fresh = Date.now() - ts <= 90 * 24 * 3600 * 1000;
  }
  const coop = Number(src.history_cooperation_count ?? src.cooperation_count ?? 0);
  const lowCollab = newcomer || (!src.historical_match && coop <= 0);
  return { newcomer, fresh, lowCollab };
}

// YouTube search.list 无 @handle → 后端用 UC... channel_id 占 handle(保证非空 + 当 identity:
// profile_url 走 /channel/{id})。但 UC... 不是可读名,卡片应显示真频道名(channel_name→display_name)。
const YT_CHANNEL_ID_RE = /^UC[A-Za-z0-9_-]{20,}$/;
function readableCreatorName(item: VkpiKolRecallItem): string {
  const handle = cleanText(item.handle);
  const displayName = cleanText(item.display_name);
  // 优先非「频道ID」的 handle(IG/TikTok 的 @用户名)→ 再非「频道ID」的真频道名 → 兜底
  if (handle && !YT_CHANNEL_ID_RE.test(handle)) return handle;
  if (displayName && !YT_CHANNEL_ID_RE.test(displayName)) return displayName;
  return handle || displayName || "";
}

// 契合命中 tags 中文化:海外创作者发现是英文搜索词命中,这里把常见摄影/创作术语映射成中文(生僻保留原文)。
const RELEVANCE_ZH: Record<string, string> = {
  wedding: "婚礼", portrait: "人像", landscape: "风光", wildlife: "野生动物", travel: "旅行",
  street: "街拍", fashion: "时尚", product: "产品", food: "美食", sports: "运动", event: "活动",
  macro: "微距", astro: "星空", documentary: "纪实", lifestyle: "生活方式", newborn: "新生儿",
  family: "家庭", commercial: "商业", editorial: "大片", "fine art": "艺术", boudoir: "闺房写真",
  flash: "闪光灯", strobe: "影室灯", lighting: "灯光", "studio lighting": "影室布光", studio: "影棚",
  softbox: "柔光箱", lens: "镜头", camera: "相机", tripod: "三脚架", gimbal: "稳定器", drone: "无人机",
  educator: "教学", reviewer: "测评", vlogger: "Vlog", filmmaker: "影片制作", cinematographer: "摄影指导",
  photographer: "摄影师", creator: "创作者", influencer: "达人", tutorial: "教程", "how-to": "教程",
  bts: "幕后", videography: "视频", cinematic: "电影感", color: "调色", "color grading": "调色",
};
function zhTag(s: string): string {
  const k = String(s || "").trim().toLowerCase();
  return RELEVANCE_ZH[k] || s;
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
  const name = display(readableCreatorName(item) || `KOL #${item.kol_pool_id}`);
  const followers = numberLabel(item.followers);
  const score = Number(item.recall_rank_score ?? item.vector_score ?? 0);
  const relevanceFlags = Array.isArray(item.relevance_flags) ? item.relevance_flags.map(cleanText).filter(Boolean) : [];
  const tier = relevanceTier(score, cleanText(item.relevance_tier_hint) === "demote");
  const showImg = Boolean(avatar) && !imgError;
  const whyFit = cleanText(item.why_fit);
  // 三引擎产出·候选卡展示信号(全部纯只读透传,绝不触评分):
  const itemRow = item as unknown as Row;
  const fitSrc = (item.source_fields && typeof item.source_fields === "object" ? item.source_fields : {}) as Row;
  const fitBadge = contentFitBadge(itemRow.fit_verdict ?? fitSrc.fit_verdict);
  const creatorType = cleanText(itemRow.creator_type ?? fitSrc.creator_type);
  const exposure = exposureLabel(item);
  const marks = freshnessMarks(item);
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
          {display(item.platform, "unknown")} · {item.type_label || item.profile_type || "profile"}{followers ? ` · ${followers} 粉/播` : ""}
        </span>
        {/* 三引擎徽章行:内容契合判定(已深析)+ 预估曝光 + 新人/新鲜/低合作 */}
        {(fitBadge || exposure || marks.newcomer || marks.fresh || marks.lowCollab) ? (
          <span className="mt-1 flex flex-wrap items-center gap-1">
            {fitBadge ? (
              <span className={`rounded-full border px-1.5 py-0.5 text-[8.5px] font-medium ${fitBadge.cls}`} title={creatorType ? `内容契合:${fitBadge.label} · ${creatorType}` : `内容契合判定:${fitBadge.label}`}>
                契合·{fitBadge.label}
              </span>
            ) : null}
            {exposure ? (
              <span className="inline-flex items-center gap-0.5 rounded border border-sky-300/25 bg-sky-400/[0.08] px-1 text-[8.5px] font-medium text-sky-100/90" title="预估曝光/触达潜力(均播放量,纯展示)">
                <TrendingUp size={8} /> {exposure} 曝光
              </span>
            ) : null}
            {marks.newcomer ? (
              <span className="inline-flex items-center gap-0.5 rounded border border-emerald-300/30 bg-emerald-400/[0.10] px-1 text-[8.5px] font-medium text-emerald-100" title="全网新发现、库内尚无 · 优先新人">
                <UserPlus size={8} /> 新人
              </span>
            ) : null}
            {marks.fresh ? (
              <span className="rounded border border-cyan-300/25 bg-cyan-400/[0.08] px-1 text-[8.5px] font-medium text-cyan-100/90" title="近 90 天有新作">新鲜</span>
            ) : null}
            {marks.lowCollab && !marks.newcomer ? (
              <span className="rounded border border-violet-300/25 bg-violet-400/[0.08] px-1 text-[8.5px] font-medium text-violet-100/90" title="历史无合作记录 · 成长空间">低合作</span>
            ) : null}
          </span>
        ) : null}
        {whyFit ? (
          <span className="mt-1 line-clamp-2 block text-[10px] leading-snug text-cyan-200/85">{whyFit}</span>
        ) : null}
        {Array.isArray(fitSrc.relevance_hits) && (fitSrc.relevance_hits as unknown[]).length ? (
          <span className="mt-1 inline-flex flex-wrap items-center gap-1" title="persona 相关度命中词(为何契合,纯展示)">
            <span className="text-[8.5px] text-slate-500">契合命中</span>
            {(fitSrc.relevance_hits as unknown[]).slice(0, 4).map((h, i) => (
              <span key={`${cleanText(h)}-${i}`} className="rounded border border-sky-300/25 bg-sky-400/[0.08] px-1 text-[8.5px] font-medium text-sky-100/90">
                {zhTag(cleanText(h))}
              </span>
            ))}
          </span>
        ) : null}
        {relevanceFlags.length ? (
          <span className="mt-1 flex flex-wrap gap-1">
            {relevanceFlags.map((flag) => (
              <span key={flag} className="rounded border border-amber-300/25 bg-amber-400/[0.08] px-1 text-[8.5px] font-medium text-amber-200/85">
                {zhTag(flag)}
              </span>
            ))}
          </span>
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
  // 产品定位(说人话):优先 LLM product_positioning,缺则用 persona。
  const positioning = display(plan.product_positioning, "") || display(plan.target_persona, "");
  const persona = display(plan.target_persona, "");
  const focus = Array.isArray(plan.product_focus) ? plan.product_focus.map(cleanText).filter(Boolean).slice(0, 4) : [];
  const avoid = Array.isArray(plan.avoid_types) ? plan.avoid_types.map(cleanText).filter(Boolean).slice(0, 4) : [];
  return (
    <div className="mb-2 rounded-md border border-cyan-300/12 bg-cyan-400/[0.045] px-2.5 py-2">
      {/* 产品定位:这是什么产品、价位、给谁的(说人话,不暴露 SKU 技术腔) */}
      {positioning ? (
        <div className="text-[10.5px] leading-relaxed text-slate-200">{positioning}</div>
      ) : null}
      <div className="mt-1 truncate text-[10px] text-slate-500">检索词:{searchQuery}</div>
      {persona && persona !== positioning ? (
        <div className="mt-0.5 truncate text-[9.5px] text-slate-600">{persona}</div>
      ) : null}
      {focus.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1">
          <span className="self-center text-[9px] text-emerald-300/70">理想</span>
          {focus.map((item) => (
            <span key={item} className="rounded border border-emerald-300/15 bg-emerald-400/[0.07] px-1.5 py-0.5 text-[9.5px] text-emerald-100/80">
              {item}
            </span>
          ))}
        </div>
      ) : null}
      {avoid.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          <span className="self-center text-[9px] text-rose-300/70">规避</span>
          {avoid.map((item) => (
            <span key={item} className="rounded border border-rose-300/15 bg-rose-400/[0.07] px-1.5 py-0.5 text-[9.5px] text-rose-100/75 line-through decoration-rose-300/40">
              {item}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// #25 bio 行:英文 bio 显「译中文」按钮 → translateBio(后端预算闸+按原文缓存);译空回退原文,中文 bio 不显按钮。
function BioLine({ bio, apiToken }: { bio: string; apiToken?: string }) {
  const [zh, setZh] = useState("");
  const [busy, setBusy] = useState(false);
  const [tried, setTried] = useState(false);
  const translate = async (ev: any) => {
    if (ev && ev.stopPropagation) ev.stopPropagation();
    if (busy || tried || !apiToken || !bio) return;
    setBusy(true);
    try {
      const res: any = await translateBio(apiToken, bio);
      if (res && res.translated) setZh(res.translated);
    } catch { /* 回退原文 */ }
    finally { setBusy(false); setTried(true); }
  };
  return (
    <div className="mt-1">
      <p className="line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{zh || bio}</p>
      {!zh && /[A-Za-z]/.test(bio) ? (
        <button
          type="button"
          onClick={translate}
          disabled={busy || tried}
          className="mt-0.5 text-[9px] text-cyan-300/70 hover:text-cyan-200 disabled:text-slate-600"
        >
          {busy ? "翻译中…" : tried ? "(暂不可译)" : "译中文"}
        </button>
      ) : null}
    </div>
  );
}

function ProfileInfoCard({ data, onOpen, apiToken }: { data: Row; onOpen?: () => void; apiToken?: string }) {
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
  const clickable = Boolean(onOpen);
  // P7:可点卡片打开右侧 KOL 详情抽屉。用 div+role 而非 <button>,避免把内部的真链接 <a> 嵌进按钮(非法嵌套)。
  return (
    <div
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? onOpen : undefined}
      onKeyDown={clickable ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen?.(); } } : undefined}
      title={clickable ? "打开 KOL 详情" : undefined}
      className={`mt-2 flex items-start gap-3 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2${clickable ? " cursor-pointer transition-colors hover:border-cyan-300/30 hover:bg-cyan-400/[0.04] focus:outline-none focus:ring-1 focus:ring-cyan-300/30" : ""}`}
    >
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
        {bio ? <BioLine bio={bio} apiToken={apiToken} /> : null}
        {profileUrl ? (
          <a
            href={profileUrl}
            target="_blank"
            rel="noreferrer noopener"
            onClick={(event) => event.stopPropagation()}
            className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
          >
            {profileUrl}
          </a>
        ) : null}
      </div>
      {clickable ? (
        <span className="shrink-0 self-center text-[9px] text-cyan-300/70">查看详情 →</span>
      ) : null}
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

// A1·URL 视频内联深析。纯读 final_v1 + final_v1_keyframe_qa 两份缓存(no-store),
// 渲染:画面质量分 layer6(content_quality_score 内容质量 / marketing_value 投放价值)、
// 分镜时间线 layer1、三观/归因/建议 layer3-5(复用 DeepLayersSection)、关键帧 QA。
// 绝不触 viltrox_fit_score:此处只读 final_v1/QA,从不写任何评分。
function VideoSceneAnalysis({ apiToken, evidenceId }: { apiToken: string; evidenceId: string }) {
  const [entry, setEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  const [qaEntry, setQaEntry] = useState<VkpiKolVideoAnalysisCacheEntry | null>(null);
  useEffect(() => {
    let cancelled = false;
    setEntry(null);
    setQaEntry(null);
    if (!apiToken || !evidenceId) return undefined;
    getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1")
      .then((res) => {
        if (cancelled) return;
        if (res.state === "ready" && res.entry) setEntry(res.entry);
      })
      .catch(() => {
        // 静默降级:无缓存/读取失败则不渲染分析框,不打断视频展示。
      });
    // 关键帧 QA 是独立 derive_method;缺它不影响 final_v1 主体渲染(独立 try)。
    getKolVideoAnalysisCache(apiToken, evidenceId, "video_analysis_final_v1_keyframe_qa")
      .then((res) => {
        if (cancelled) return;
        if (res.state === "ready" && res.entry) setQaEntry(res.entry);
      })
      .catch(() => {
        // QA 缺失静默:只是少一块复核信息,不阻断主分析展示。
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, evidenceId]);
  const result = asRecord(entry?.result);
  const payload = asRecord(result.video_analysis_final_v1).layer1_visual_content ? asRecord(result.video_analysis_final_v1) : result;
  const layer1 = asRecord(payload.layer1_visual_content);
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer4 = asRecord(payload.layer4_attribution);
  const layer5 = asRecord(payload.layer5_recommendations);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  // 画面质量分:content_quality_score=内容质量,marketing_value=投放价值(口径与 KOLVideoAnalysisPanel.AnalysisCard 一致)。
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || textFrom(layer6.key_hook);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const riskText = textFrom(layer6.risk_flags);
  const contentSummary = cleanText(layer1.content_summary);
  const sceneTimeline = sceneTimelineRowsLocal(layer1.scene_timeline);
  const hasScores = contentScore.score != null || marketingScore.score != null;
  // 关键帧 QA(复用面板口径):pass/checks/issues/纠偏建议。
  const qaPayload = finalV1QaPayload(qaEntry);
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaPass = qaBoolean(qaPayload.qa_pass ?? asRecord(qaEntry?.result).qa_pass);
  const qaBadgeText = qaPass === false ? "需复核" : qaPass === true ? "通过" : "未定";
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  if (!hasScores && !contentSummary && !sceneTimeline.length && !qaHasPayload) return null;
  return (
    <div className="mt-2 space-y-2">
      {hasScores ? (
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border border-white/[0.05] bg-black/25 px-2.5 py-2">
            <div className="mb-1 text-[9px] text-slate-500">内容质量</div>
            <div className="text-[22px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(contentScore.score) }}>{contentScore.score ?? "—"}</div>
          </div>
          <div className="rounded-md border border-white/[0.05] bg-black/25 px-2.5 py-2">
            <div className="mb-1 text-[9px] text-slate-500">投放价值</div>
            <div className="text-[22px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(marketingScore.score) }}>{marketingScore.score ?? "—"}</div>
          </div>
        </div>
      ) : null}
      {verdict ? (
        <div className="text-[10.5px] leading-relaxed text-slate-300">{compactText(verdict, 180)}</div>
      ) : null}
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
      {viewerReaction || riskText ? (
        <div className="flex flex-wrap gap-1.5 text-[9.5px]">
          {viewerReaction ? <span className="rounded bg-purple-500/10 px-2 py-1 text-purple-200">心动: {compactText(viewerReaction, 54)}</span> : null}
          {riskText ? <span className="rounded bg-amber-500/10 px-2 py-1 text-amber-200">风险: {compactText(riskText, 60)}</span> : null}
        </div>
      ) : null}
      <DeepLayersSection layer3={layer3} layer4={layer4} layer5={layer5} />
      {qaHasPayload ? (
        <div className={`rounded-md border p-2 ${qaPass === false ? "border-rose-400/20 bg-rose-500/[0.045]" : "border-emerald-400/15 bg-emerald-500/[0.035]"}`}>
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <span className={`inline-flex items-center gap-1 rounded px-2 py-1 text-[9px] font-medium ${qaPass === false ? "bg-rose-500/15 text-rose-200" : "bg-emerald-500/15 text-emerald-200"}`}>
              {qaPass === false ? <AlertTriangle size={10} /> : <ShieldCheck size={10} />}
              关键帧 QA {qaBadgeText}
            </span>
            {Number.isFinite(qaConfidence) ? <span className="text-[9px] text-slate-500">置信 {Math.round(qaConfidence * 100)}%</span> : null}
          </div>
          {qaSummary ? <div className="mb-1.5 text-[10px] leading-relaxed text-slate-200">{compactText(qaSummary, 150)}</div> : null}
          {qaChecks.length ? (
            <div className="mb-1.5 flex flex-wrap gap-1">
              {qaChecks.map((check) => (
                <span key={check.key} className={`rounded border px-1.5 py-0.5 text-[8.5px] ${qaStatusClass(check.status)}`} title={check.detail || undefined}>
                  {check.label}: {qaStatusLabel(check.status)}
                </span>
              ))}
            </div>
          ) : null}
          {qaIssues.slice(0, 2).map((issue) => (
            <div key={issue.key} className="mb-1 rounded border border-white/[0.05] bg-black/20 px-2 py-1 text-[9.5px] text-slate-300">
              <span className="text-amber-200">{issue.label}</span>
              {issue.evidence ? <span> · {compactText(issue.evidence, 90)}</span> : null}
              {issue.correction ? <span className="text-cyan-200"> · {compactText(issue.correction, 70)}</span> : null}
            </div>
          ))}
          {qaCorrection ? <div className="text-[9.5px] text-slate-400">纠偏建议: {compactText(qaCorrection, 150)}</div> : null}
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
  onOpenProfile,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
  apiToken: string;
  canExecute: boolean;
  isExecuting: boolean;
  onExecute: () => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
}) {
  const profileFlow = asRecord(result.profile_flow);
  const videoFlow = asRecord(result.video_flow);
  const creator = asRecord(result.creator_identity || videoFlow.creator_identity);
  const metadata = asRecord(result.video_metadata || videoFlow.video_metadata);
  const analysis = asRecord(videoFlow.analysis);
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
    ? "没识别到创作者，无法建档。"
    : result.url_type === "unknown"
      ? "识别不了这个链接。"
      : "";

  // P7·账号 URL 结果卡:把后端自动抓取的基础资料(头像/粉丝/简介/帖数)合并展示。
  // 优先 profile_flow.profile_data(execute 后写入),缺则用 creator_identity / 顶层 result 字段兜底,
  // 缺值诚实留空,绝不编造粉丝数。点卡片打开右侧 KOL 详情抽屉(onOpenProfile)。
  const profileData = asRecord(profileFlow.profile_data);
  const profileBasics: Row = {
    avatar_url: profileData.avatar_url ?? creator.avatar_url,
    handle: profileData.handle ?? creator.handle ?? result.handle,
    platform: profileData.platform ?? creator.platform ?? result.platform,
    followers: profileData.followers ?? creator.followers ?? creator.subscriber_count,
    posts_count: profileData.posts_count ?? creator.posts_count,
    bio: profileData.bio ?? creator.bio ?? creator.description,
    profile_url: profileData.profile_url ?? creator.profile_url ?? creator.channel_url ?? result.url?.normalized,
  };
  const hasProfileBasics = !isVideo && [
    profileBasics.avatar_url,
    profileBasics.followers,
    profileBasics.posts_count,
    profileBasics.bio,
    profileBasics.handle,
  ].some((value) => cleanText(value));
  const canOpenProfile = !isVideo && Boolean(onOpenProfile);

  // 项⑥:profile 默认只抓资料(profile_basics),不发现视频。这个按钮用 account_deep+force_full_history
  // 把该 KOL 全部历史视频 materialize,再 enqueueAllKolVideos 跑 final_v1。
  const [fullVideoState, setFullVideoState] = useState<{ status: string; msg: string }>({ status: "idle", msg: "" });
  const matchedKolId = (result as any).matched_kol_pool_id;
  // 刀2·流2 路A:profile execute 顺带入队了 N 条代表视频 final_v1(account_dossier 据此出 LLM 账号分)。
  // queued>0 → 深度分析进行中,据此诚实化完成横幅(头像粉丝已入库,但 LLM 分要等 worker 跑完代表视频)。
  const repAnalysis = asRecord((result as any).representative_video_analysis);
  const repQueued = Number(repAnalysis.queued ?? 0);
  const deepAnalysisRunning = !isVideo && repQueued > 0;
  const discoverAllVideos = async () => {
    const url = cleanText(result.url?.input);
    if (!apiToken || !url || fullVideoState.status === "loading") return;
    setFullVideoState({ status: "loading", msg: "正在发现该 KOL 全部历史视频…" });
    try {
      const r = await deepCrawlKolUrl(apiToken, url, true, {
        mode: "account_deep", forceFullHistory: true, maxPosts: 120,
        source: "smart_kol_input_full_video", timeoutMs: 300000,
      });
      const kid = (r as any).profile_flow?.kol_pool_id || matchedKolId;
      if (kid) {
        const enq = await enqueueAllKolVideos(apiToken, kid);
        setFullVideoState({ status: "done", msg: `已发现并入队:${enq.queued ?? 0} 条 final_v1 排队中(进度见左侧任务板)` });
      } else {
        setFullVideoState({ status: "done", msg: "已发现视频并入库,稍后在抽屉查看" });
      }
    } catch (e: any) {
      setFullVideoState({ status: "error", msg: e?.message ? String(e.message) : "全视频发现失败" });
    }
  };

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
              {result.in_pool ? "库内已有此人" : "库内暂无此人"}
            </span>
            {tiktokRisk ? (
              <span className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1 text-amber-100">
                TikTok 视频有时拿不到，可能需要重试
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
          {/* P7·账号 URL 结果卡:头像 + 粉丝(+帖数/简介,有则显)+ 点开右侧详情抽屉;缺值诚实留空,不编造。 */}
          {hasProfileBasics ? (
            <ProfileInfoCard
              data={profileBasics}
              apiToken={apiToken}
              onOpen={canOpenProfile ? () => onOpenProfile?.(result) : undefined}
            />
          ) : null}
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
            // A1·evidenceId 口径对齐:只用 video 证据 id 查 video 缓存。
            // 移除 matched_kol_pool_id fallback——KOL 池 id 不是 video 证据 id,拿它查会命中错缓存。缺 evidence_id 则 VideoSceneAnalysis 自身静默不渲染。
            <VideoSceneAnalysis
              apiToken={apiToken}
              evidenceId={String(videoFlow.evidence_id ?? "").trim()}
            />
          ) : null}
        </div>
      ) : null}
      {disabledReason ? (
        <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1.5 text-[10.5px] text-amber-100">
          {disabledReason}
        </div>
      ) : null}
      {executeDone ? (
        <div className={`mt-2 flex items-center gap-2 rounded-md border px-2 py-1.5 text-[10.5px] ${
          flowStatus === "partial"
            ? "border-amber-300/20 bg-amber-400/[0.10] text-amber-100"
            : "border-emerald-300/20 bg-emerald-400/[0.10] text-emerald-100"
        }`}>
          <span className="flex-1">
            {flowStatus === "partial"
              ? (isVideo ? "视频分析部分完成，已入库" : "资料部分抓取完成，已入库")
              : (isVideo
                  ? "视频分析完成，已入库"
                  : deepAnalysisRunning
                    ? `资料已入库 · 账号深度分析进行中(${repQueued} 条代表视频，完成后「查看完整分析」即出 LLM 账号分)`
                    : "资料已抓取并入库")}
            {latency ? ` · 耗时 ${latency}` : ""}
          </span>
          {!isVideo ? (
            <button
              type="button"
              disabled={fullVideoState.status === "loading"}
              onClick={() => void discoverAllVideos()}
              className="shrink-0 rounded border border-cyan-300/30 bg-cyan-400/[0.12] px-2 py-0.5 font-medium text-cyan-50 hover:bg-cyan-400/[0.2] disabled:opacity-50"
            >
              {fullVideoState.status === "loading" ? "发现中…" : "发现并分析全部视频"}
            </button>
          ) : null}
          {onOpenProfile && result.matched_kol_pool_id ? (
            <button
              type="button"
              onClick={() => onOpenProfile(result)}
              className="shrink-0 rounded border border-emerald-300/30 bg-emerald-400/[0.12] px-2 py-0.5 font-medium text-emerald-50 hover:bg-emerald-400/[0.2]"
            >
              查看完整分析 →
            </button>
          ) : null}
        </div>
      ) : null}
      {fullVideoState.msg ? (
        <div className={`mt-2 rounded-md border px-2 py-1.5 text-[10.5px] ${
          fullVideoState.status === "error" ? "border-rose-300/20 bg-rose-500/[0.08] text-rose-100" : "border-cyan-300/20 bg-cyan-400/[0.08] text-cyan-100"
        }`}>{fullVideoState.msg}</div>
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
  onOpenProfile,
}: {
  apiToken?: string;
  onRecallItems?: (items: VkpiKolRecallItem[]) => void;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
}) {
  // 挂载时回填上次激活搜索的展示态(sessionStorage),让 90s/10min 父刷新若偶发重挂本面板时
  // ①②③ 结果与轮询不凭空消失;无持久化则回到正常初始态。
  const persistedDisplay = useMemo(() => readPersistedSearchDisplay(), []);
  const [input, setInput] = useState(() => persistedDisplay?.input ?? "");
  const [state, setState] = useState<State>(() => (persistedDisplay?.recallResult || persistedDisplay?.urlResult ? "ready" : "idle"));
  const [mode, setMode] = useState<Mode>(() => persistedDisplay?.mode ?? "idle");
  const [urlResult, setUrlResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(() => persistedDisplay?.urlResult ?? null);
  const [recallResult, setRecallResult] = useState<VkpiKolRecallResponse | null>(() => persistedDisplay?.recallResult ?? null);
  const [advanceResult, setAdvanceResult] = useState<VkpiKolSmartSearchProfileAdvanceResponse | null>(null);
  const [activeSearchSessionId, setActiveSearchSessionId] = useState<number | null>(() => persistedDisplay?.activeSearchSessionId ?? null);
  const [activeSearchSession, setActiveSearchSession] = useState<VkpiKolSearchHistoryItem | null>(() => persistedDisplay?.activeSearchSession ?? null);
  const [sessionPollNotice, setSessionPollNotice] = useState("");
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  // 问题2 平台选择器:默认全选已落地的 YT/IG/TikTok(FB 待 provider 落地,UI 置灰)。
  const [discoveryPlatforms, setDiscoveryPlatforms] = useState<string[]>(["youtube", "instagram", "tiktok"]);
  // 区域语言本地化:目标市场码(空=全球英文;JP/KR/DE/… 按该区语言搜平台捞本地达人)。
  const [discoveryRegion, setDiscoveryRegion] = useState<string>("");
  // 刀1·流3 恒开(2026-06-16):全网发现不再挂开关,任何文字搜索都自动触发(见 run() 的 queueTextAdvance)。
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
  const discoveryItems = useMemo(() => {
    const all = discoveryItemsFromSession(activeSearchSession);
    // 平台筛选即时生效:按当前勾选的 discoveryPlatforms 过滤已展示的发现结果(不必重搜)。
    // platform 字段缺失的未分类项放行,避免因字段缺失误藏。全不勾选时仅余未分类项。
    return all.filter((it) => {
      if (looksLikeRetailer(it)) return false;  // 滤掉 B&H 等零售/经销实体,非真创作者
      const p = String(it.platform || "").toLowerCase();
      return !p || discoveryPlatforms.includes(p);
    });
  }, [activeSearchSession, discoveryPlatforms]);
  // 三框·框1:LLM 人群理解可编辑(防 LLM 理解偏)——编辑后「用此重搜」。
  const [personaEditing, setPersonaEditing] = useState(false);
  const [personaDraft, setPersonaDraft] = useState("");
  const activeSessionCounts = sessionAdvanceCounts(activeSearchSession);
  const activeSessionSummary = asRecord(activeSearchSession?.result_summary);
  const activeSmartJob = asRecord(activeSessionSummary.smart_search_profile_advance_job);
  const activeSessionStatus = cleanText(activeSmartJob.advance_status || activeSmartJob.status || activeSearchSession?.status);
  // F-display:AI 规划退回基础检索的诚实提示。读 result_summary.smart_search_profile_advance_job
  // .query_plan_source ∈ {llm_plan, rule_v0_fallback};仅当存在且等于 rule_v0_fallback 才提示,
  // 字段缺失/为 llm_plan 时静默不渲染(graceful absence,不编造)。
  const plannerFellBack = cleanText(activeSmartJob.query_plan_source) === "rule_v0_fallback";
  // 诚实会话横幅(排队/查找中/已完成/部分完成/未完成)——只读后端真有字段,见 sessionStatusBanner。
  // advanceResult?.status:queueTextAdvance 刚返回、尚未首拍轮询时的即时状态兜底(queued/...)。
  const sessionBanner = useMemo(
    () => sessionStatusBanner(activeSearchSession, activeSessionStatus || cleanText(advanceResult?.status), activeSessionCounts, Boolean(activeSearchSessionId)),
    [activeSearchSession, activeSessionStatus, advanceResult, activeSessionCounts, activeSearchSessionId],
  );

  useEffect(() => {
    if (recallItems.length) onRecallItems?.(recallItems);
  }, [recallItems, onRecallItems]);

  // 搜索展示态持久化:任一 ①②③ 相关态变化就写回 sessionStorage(兜底重挂恢复);
  // 全空(无召回/无 URL 结果/无激活会话)时清掉,避免回填到一个空壳搜索框。
  useEffect(() => {
    if (!recallResult && !urlResult && !activeSearchSession && !activeSearchSessionId) {
      writePersistedSearchDisplay(null);
      return;
    }
    writePersistedSearchDisplay({ input, mode, recallResult, urlResult, activeSearchSession, activeSearchSessionId });
  }, [input, mode, recallResult, urlResult, activeSearchSession, activeSearchSessionId]);

  const refreshHistory = useCallback(async () => {
    if (!apiToken) {
      setHistoryItems([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const response = await listKolSearchHistory(apiToken, { limit: 20, itemLimit: 5 });
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
    const queryType = cleanText(session.query_type);
    if (queryType === "url_video" || queryType === "url_profile") {
      setActiveSearchSession(session);
      setMode("url");
      setUrlResult(urlResultFromSession(session));
      setRecallResult(null);
      return;
    }
    setMode("text");
    // 框3(全网发现)非破坏式覆盖:discoveryItems 派生自 activeSearchSession 的 new_creator 项;
    // 轮询头几拍常拿到尚无发现项的会话快照(running/部分写入)。若无条件覆盖,会把已点亮的框3
    // 刷成空 → 看起来「全网发现整块消失」。故:已有发现项时,空发现的轮询快照不覆盖会话(保住框3);
    // 但仍合并后端最新 result_summary/status,让横幅与计数继续推进。
    setActiveSearchSession((prev) => {
      const prevDiscovery = discoveryItemsFromSession(prev).length;
      const nextDiscovery = discoveryItemsFromSession(session).length;
      // 保住已点亮的框3:新快照发现项更少时(轮询时序/异步落库导致),合并后端最新 status/summary
      // 但保留 prev 更全的 items,绝不让更稀的快照把已显示的发现项刷没。
      if (prevDiscovery > nextDiscovery) {
        return { ...prev, status: session.status, result_summary: session.result_summary };
      }
      return session;
    });
    // 框2(库内召回)非破坏式覆盖:run() 触发的全网发现会建一个 advance 会话并启动轮询,其 recall
    // items 由后台 worker 异步写入,轮询头几拍常拿到 running/空会话。若无条件覆盖,会把 run() 首屏已
    // 渲染的库内召回刷成空 → 看起来「用户列表整块消失」。故:空轮询不得覆盖已有的非空召回(保住首屏);
    // 但当前无召回(null/空)时仍允许用轮询结果点亮结果区,以便框3 全网发现能显示。
    // 框1(产品人群分析)保活:recallResultFromSession 不带 llm_query_plan(那只在实时 smartKolSearch
    // 响应里有),若直接替换会把已渲染的 ① PlanPills 刷没 → 单独把上次的 llm_query_plan 透传进来。
    const polledRecall = recallResultFromSession(session);
    const polledRecallCount =
      (polledRecall.buckets?.creator?.length || 0) + (polledRecall.buckets?.reviewer?.length || 0);
    setRecallResult((prev) => {
      const prevCount =
        (prev?.buckets?.creator?.length || 0) + (prev?.buckets?.reviewer?.length || 0);
      const prevPlan = (prev as Row | null)?.llm_query_plan;
      const merged = prevPlan && !(polledRecall as unknown as Row).llm_query_plan
        ? ({ ...polledRecall, llm_query_plan: prevPlan } as VkpiKolRecallResponse)
        : polledRecall;
      if (polledRecallCount === 0 && prevCount > 0) return prev;
      return merged;
    });
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
    let terminalSince: number | null = null;  // 本会话内「首次见终态」时间(闭包,随会话重置)
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;
    const poll = async () => {
      try {
        const session = await getKolSearchSession(apiToken, activeSearchSessionId);
        if (cancelled) return;
        applyPolledSession(session);
        // 发现项常在 session 状态置「终态」之后才异步落库 → 不能一见终态就停(否则框3 卡 0,正是你看到的)。
        // 改:终态后宽限继续轮询,直到发现项真的到 / 宽限 30s 用尽 / 总超时,才真正宣告已找完。
        const haveDiscovery = discoveryItemsFromSession(session).length > 0;
        const timedOut = Date.now() - startedAt > maxPollMs;
        if (isSearchSessionTerminal(session)) {
          if (terminalSince == null) terminalSince = Date.now();
          const graceUsedUp = Date.now() - terminalSince >= 30000;
          if (haveDiscovery || graceUsedUp || timedOut) {
            setActiveSearchSessionId(null);
            setSessionPollNotice("已找完，结果已更新");
            void refreshHistory();
            return;
          }
          // 终态但发现项未到 + 宽限期内 → 继续轮询
        } else if (timedOut) {
          setActiveSearchSessionId(null);
          setSessionPollNotice("仍在后台查找，可从最近历史或任务里继续查看");
          void refreshHistory();
        }
      } catch (err) {
        if (cancelled) return;
        setSessionPollNotice(err instanceof Error ? err.message : "同步失败，稍后会自动重试");
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
      let autoVideo: VkpiKolUrlDeepCrawlResponse | null = null;
      if (!isText) {
        setMode("url");
        const urlPayload = response.result as VkpiKolUrlDeepCrawlResponse;
        setUrlResult(urlPayload);
        // 账号 URL 自动入库:识别为 profile 且后端 dry-run 就绪(dry_run_ready)→ 直接自动 execute
        // (mode profile_basics:只抓基础资料 + 入库,不触发昂贵视频深析),前端随后展示 ProfileInfoCard。
        if (
          urlPayload.url_type === "profile" &&
          !urlPayload.execute &&
          cleanText(asRecord(urlPayload.profile_flow).status) === "dry_run_ready"
        ) {
          autoProfile = urlPayload;
        }
        // 刀1·流1(2026-06-16):video URL 也自动 execute,贴视频链接不必再点「只分析此视频/建档并分析」。
        // 门槛与 urlCanExecute 的 video 正常分支一致:创作者已解析 + 后端就绪 + 合法操作。runUrlExecute 对
        // video 走 video_deep,入 evidence + 排 final_v1(幂等 already_analyzed/already_queued);视频/分镜随
        // worker 完成经会话轮询自动内联回填。失败时 videoJobLastError 置位,手动重试按钮按 urlCanExecute 自然复现。
        if (!autoProfile && urlPayload.url_type === "video" && !urlPayload.execute) {
          const vFlow = asRecord(urlPayload.video_flow);
          const vCreator = asRecord(urlPayload.creator_identity || vFlow.creator_identity);
          const vStatus = cleanText(asRecord(urlPayload.profile_flow).status || vFlow.status);
          const pOp = cleanText(asRecord(urlPayload.profile_flow).operation);
          const rawVOp = cleanText(vFlow.operation);
          const vOp = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(pOp)
            ? pOp
            : rawVOp || pOp;
          const vCreatorResolved = Boolean(
            cleanText(vFlow.creator_resolution_status) === "resolved" ||
            cleanText(
              vCreator.handle ||
                vCreator.channel_id ||
                vCreator.profile_url ||
                urlPayload.handle ||
                urlPayload.channel_id,
            ),
          );
          if (
            ["dry_run_ready", "ready_to_execute"].includes(vStatus) &&
            vCreatorResolved &&
            ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(vOp)
          ) {
            autoVideo = urlPayload;
          }
        }
      } else {
        setMode("text");
        setRecallResult(response.result as VkpiKolRecallResponse);
      }
      setState("ready");
      void refreshHistory();
      // 刀1·流3(2026-06-16)恒开:任何文字搜索都自动触发全网发现(advance-job 全量,含所选平台),
      // 不再挂在「深度查找」开关上 →「先库内召回 → 再全网发现」一步到位,本地+线上首屏同呈。
      // 预算护栏 enforce 兜底超支(已确认放行)。
      if (isText) void queueTextAdvance(overrideQuery);
      // 账号 URL 自动抓资料 + 入库(不再弹「抓基础资料」二次确认)。
      if (autoProfile) void runUrlExecute(autoProfile, { auto: true });
      // 刀1·流1:video URL 自动入 evidence + 排 final_v1(不再弹「只分析此视频」二次确认)。
      if (autoVideo) void runUrlExecute(autoVideo, { auto: true });
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "请求失败，请重试");
    }
  };

  // URL 执行核心:source 显式传当次结果(避免 setUrlResult 后读到旧 state),auto=true 为自动跑。
  // 刀2·流2 路A(2026-06-16):profile 改用 mode "profile_with_video"(原 profile_basics)——抓基础资料 +
  // 入库 + 自动跑 PROFILE_REP_VIDEO_LIMIT 条代表视频 final_v1,dossier 才出真 LLM 账号分(原 profile_basics
  // 不分析视频→llm_v6_fit=None,只有空 dossier)。后端 _profile_should_enqueue_representative_videos 认
  // profile_with_video;TikTok 代表视频暂被后端 skip(resolver 未修)。video 仍走 video_deep。
  // V6 Fit 由 write_kol_profile_basics 白名单兜底不触碰 viltrox_fit_score。
  const runUrlExecute = async (source: VkpiKolUrlDeepCrawlResponse, opts: { auto?: boolean } = {}) => {
    const query = cleanText(source.url?.input || input);
    if (!apiToken || !query) return;
    const sourceProfileFlow = asRecord(source.profile_flow);
    setState("executing");
    setError("");
    try {
      const isVideo = source.url_type === "video";
      const executeMode = isVideo ? "video_deep" : "profile_with_video";
      const sessionId = sessionIdFrom(source.search_session);
      const response = await deepCrawlKolUrl(apiToken, query, true, {
        maxPosts: typeof sourceProfileFlow.max_posts === "number" ? sourceProfileFlow.max_posts : 3,
        mode: executeMode,
        ...(isVideo ? {} : { representativeVideoLimit: PROFILE_REP_VIDEO_LIMIT }),
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
        market: discoveryRegion,
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
        setSessionPollNotice("后台查找中...");
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "全网查找启动失败，请重试");
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
              <h2 className="text-[12px] font-semibold text-white">找达人</h2>
            </div>
            <div className="mt-0.5 truncate text-[10px] text-slate-600">
              贴主页/视频链接看资料，或描述产品需求找人。
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
          {inferredMode === "url" ? "查看" : "查找"}
        </button>
      </form>

      {state === "idle" && !input ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[9.5px] text-slate-600">
          <span className="inline-flex items-center gap-1 text-cyan-100"><Video size={9} /> 视频 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-violet-100"><BadgeCheck size={9} /> 账号 URL</span>
          <span className="text-slate-700">/</span>
          <span className="inline-flex items-center gap-1 text-emerald-100"><Search size={9} /> 产品需求</span>
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
          onOpenProfile={onOpenProfile}
        />
      ) : null}

      {mode === "text" && recallResult ? (
        <div className="mt-3 space-y-2.5">
          {/* 框1 · 产品人群分析(可编辑,防 LLM 理解偏) */}
          <div className="rounded-lg border border-cyan-300/15 bg-cyan-400/[0.04] p-3">
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="text-[11px] font-medium text-cyan-100">① 要找什么样的人</div>
              {!personaEditing ? (
                <button
                  type="button"
                  onClick={() => { setPersonaDraft(display(llmPlan.search_query, cleanText(input))); setPersonaEditing(true); }}
                  className="rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-white/[0.2] hover:text-white"
                >编辑</button>
              ) : null}
            </div>
            {plannerFellBack ? (
              <div className="mb-1.5 flex items-start gap-1.5 rounded-md border border-amber-300/20 bg-amber-400/[0.07] px-2 py-1.5 text-[10px] leading-relaxed text-amber-100/90">
                <Info size={11} className="mt-0.5 shrink-0" />
                <span>AI 规划暂不可用,已用基础检索匹配。下方结果可正常查看,稍后可重试以获得更精准的人群理解。</span>
              </div>
            ) : null}
            {personaEditing ? (
              <div className="space-y-1.5">
                <textarea
                  value={personaDraft}
                  onChange={(event) => setPersonaDraft(event.target.value)}
                  rows={2}
                  className="w-full resize-none rounded-md border border-white/[0.1] bg-black/30 px-2 py-1.5 text-[11px] text-white placeholder-slate-600 focus:border-cyan-400/40 focus:outline-none"
                  placeholder="描述要找什么样的人，例如:35mm 低光人像 YouTube 摄影师…"
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
              <div className="text-[10px] text-slate-500">点「编辑」改写要找的人群，再「用此重搜」。</div>
            )}
          </div>

          {/* 框2 · 库内账号匹配 */}
          <div className="rounded-lg border border-violet-300/15 bg-violet-950/[0.10] p-3">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="text-[11px] font-medium text-violet-100">② 库内已有的人 · {display(recallResult.diagnostics?.candidate_count)} 个</div>
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

          {/* 框3 · 全网发现(Apify+平台,带头像)· 优先新人主源,描边更亮 */}
          <div className="rounded-lg border border-emerald-300/30 bg-emerald-950/[0.16] p-3 ring-1 ring-emerald-300/10">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/30 bg-emerald-400/[0.12] px-1.5 py-0.5 text-[8.5px] font-semibold text-emerald-100">
                  <UserPlus size={9} /> 优先新人
                </span>
                <div className="text-[11px] font-semibold text-emerald-100">③ 全网新发现的人{discoveryItems.length ? ` · ${discoveryItems.length} 个` : ""}</div>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/25 bg-emerald-400/[0.1] px-1.5 py-0.5 text-[9px] font-medium text-emerald-200/90" title="任何文字搜索都自动从所选平台发现新号,无需手点">
                <Sparkles size={9} /> 自动·恒开
              </span>
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
              <span className="rounded-full border border-white/[0.06] px-2 py-0.5 text-[10px] text-slate-600" title="Facebook 发现即将支持">Facebook · 即将</span>
              <span className="ml-1 text-[10px] text-slate-500">区域</span>
              <select
                value={discoveryRegion}
                onChange={(event) => setDiscoveryRegion(event.target.value)}
                title="目标市场:选非英语区会按该区语言搜平台、捞本地达人(改区域后点「重新全网查找」重搜生效)"
                className="rounded-md border border-white/[0.1] bg-black/30 px-1.5 py-0.5 text-[10px] text-slate-200 focus:border-cyan-400/40 focus:outline-none"
              >
                {[
                  { v: "", t: "全球·英文" },
                  { v: "JP", t: "日本·日语" },
                  { v: "KR", t: "韩国·韩语" },
                  { v: "DE", t: "德国·德语" },
                  { v: "FR", t: "法国·法语" },
                  { v: "ES", t: "西班牙·西语" },
                  { v: "IT", t: "意大利·意语" },
                  { v: "BR", t: "巴西·葡语" },
                  { v: "RU", t: "俄罗斯·俄语" },
                  { v: "TH", t: "泰国·泰语" },
                  { v: "VN", t: "越南·越语" },
                  { v: "ID", t: "印尼·印尼语" },
                ].map((o) => (
                  <option key={o.v} value={o.v} className="bg-slate-900 text-slate-100">{o.t}</option>
                ))}
              </select>
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
                重新全网查找
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
                <Loader2 size={12} className="animate-spin" /> 正在从所选平台找新号，完成后自动显示
              </div>
            ) : sessionBanner && (sessionBanner.tone === "error" || sessionBanner.tone === "warn") ? (
              // 失败/部分但无发现项:不再静默落空白占位,直接说明状态与原因(诚实兜底)。
              <div className={`rounded-md border px-3 py-2.5 text-[10.5px] leading-relaxed ${
                sessionBanner.tone === "error"
                  ? "border-rose-300/20 bg-rose-500/[0.08] text-rose-100"
                  : "border-amber-300/20 bg-amber-400/[0.08] text-amber-100"
              }`}>
                <div className="font-medium">{sessionBanner.label}</div>
                <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-3 text-center text-[10.5px] text-slate-500">全网发现恒开 · 搜索后自动从所选平台发现新号</div>
            )}
            {sessionBanner ? (
              // 诚实会话横幅:排队/查找中/已完成/部分完成/未完成 + 真原因;部分/已完成仍保留计数。
              <div className={`mt-2 rounded-md border px-2.5 py-2 text-[10px] leading-relaxed ${
                sessionBanner.tone === "error"
                  ? "border-rose-300/20 bg-rose-500/[0.07] text-rose-100"
                  : sessionBanner.tone === "warn"
                    ? "border-amber-300/20 bg-amber-400/[0.07] text-amber-100"
                    : sessionBanner.tone === "ok"
                      ? "border-emerald-300/20 bg-emerald-400/[0.07] text-emerald-100"
                      : "border-emerald-300/15 bg-black/15 text-emerald-100/75"
              }`}>
                <div className="flex flex-wrap items-center gap-1.5">
                  {sessionBanner.tone === "info" ? <Loader2 size={11} className="animate-spin" /> : null}
                  <span className="font-medium">{sessionBanner.label}</span>
                  {Object.keys(activeSessionCounts).length ? (
                    <>
                      <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已找到 {display(activeSessionCounts.ready, "0")}</span>
                      <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已入库 {display(activeSessionCounts.executed, "0")}</span>
                      {Number(activeSessionCounts.errors) > 0 || Number(activeSessionCounts.failed) > 0 ? (
                        <span className="rounded border border-rose-300/20 bg-black/15 px-1.5 py-0.5 text-rose-200/80">未完成 {display(Number(activeSessionCounts.errors || 0) + Number(activeSessionCounts.failed || 0), "0")}</span>
                      ) : null}
                    </>
                  ) : null}
                </div>
                <div className="mt-0.5 opacity-85">{sessionBanner.note}</div>
                {sessionPollNotice ? <div className="mt-0.5 opacity-70">{sessionPollNotice}</div> : null}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

    </section>
  );
}
