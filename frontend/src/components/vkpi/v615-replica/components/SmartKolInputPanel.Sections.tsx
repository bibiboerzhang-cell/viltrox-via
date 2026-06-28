// SmartKolInputPanel 展示型子组件 + 会话/召回派生器(从 SmartKolInputPanel.tsx 抽出,行为不变)。
// 容器组件本体(useState/useEffect/run 等)仍留 SmartKolInputPanel.tsx,这里只放纯派生函数 +
// 仅吃 props 的展示组件(各自局部 useState 不破坏容器 hooks 顺序)。容器 import 回去,调用点不变。
// 红线:纯展示/派生,只读 final_v1/QA 缓存,绝不写任何 viltrox_fit_score。
import { useEffect, useState } from "react";
import { AlertTriangle, BadgeCheck, Clock3, Database, Loader2, ShieldCheck, TrendingUp, UserPlus, Video } from "lucide-react";

import {
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolSearchHistoryItem,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";
import { deepCrawlKolUrl, enqueueAllKolVideos, getKolVideoAnalysisCache, translateBio, type VkpiKolVideoAnalysisCacheEntry } from "../../../../services/vkpi/kolPool-api";
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

import {
  actionDescription,
  asRecord,
  cleanText,
  display,
  durationLabel,
  numberLabel,
  terminalSessionStatus,
  urlTypeLabel,
  videoExecutionDone,
  youtubeEmbedUrl,
  type Mode,
  type Row,
} from "./SmartKolInputPanel.helpers";

type State = "idle" | "loading" | "ready" | "executing" | "error";
export const PENDING_SEARCH_SESSION_KEY = "vkpi:pendingKolSearchSessionId";
// 刀2·流2 路A:贴账号 URL 自动分析的代表视频条数(dossier 据此出 LLM 账号分)。2 = 信号与成本/排队的折中;
// 想更深可在抽屉点「发现并分析全部视频」。代表视频走交互优先(并发A tier0)插队,不被批量饿死。
export const PROFILE_REP_VIDEO_LIMIT = 2;
// 搜索展示态持久化:把当前激活搜索的 ①②③ 显示态存进 sessionStorage,挂载时回填。
// 即便父级 90s/10min 刷新偶发重挂本面板(useState 归零),也能恢复结果,不让用户的搜索凭空消失。
const ACTIVE_SEARCH_DISPLAY_KEY = "vkpi:activeKolSearchDisplay";

// 搜索展示态的持久化形状(只存渲染所需,够回填 ①②③ 与轮询续接)。
type PersistedSearchDisplay = {
  input: string;
  mode: Mode;
  recallResult: VkpiKolRecallResponse | null;
  urlResult: VkpiKolUrlDeepCrawlResponse | null;
  activeSearchSession: VkpiKolSearchHistoryItem | null;
  activeSearchSessionId: number | null;
};

export function readPersistedSearchDisplay(): PersistedSearchDisplay | null {
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

export function writePersistedSearchDisplay(value: PersistedSearchDisplay | null): void {
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

export function recallTopItems(response: VkpiKolRecallResponse | null): VkpiKolRecallItem[] {
  if (!response) return [];
  const creator = Array.isArray(response.buckets?.creator) ? response.buckets.creator : [];
  const reviewer = Array.isArray(response.buckets?.reviewer) ? response.buckets.reviewer : [];
  return [...creator.slice(0, 3), ...reviewer.slice(0, 2)];
}

export function historySessionId(value: unknown): number | undefined {
  const record = asRecord(value);
  const raw = record.id ?? record.session_id;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

export function sessionItems(session: VkpiKolSearchHistoryItem): Row[] {
  const items = Array.isArray(session.items) && session.items.length
    ? session.items
    : Array.isArray(session.items_preview)
      ? session.items_preview
      : [];
  return items.map((item) => asRecord(item));
}

export function sessionAdvanceCounts(session: VkpiKolSearchHistoryItem | null): Row {
  const summary = asRecord(session?.result_summary);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return asRecord(batch.counts || smartJob.advance_counts);
}

export function isSearchSessionTerminal(session: VkpiKolSearchHistoryItem): boolean {
  if (terminalSessionStatus(session.status)) return true;
  const summary = asRecord(session.result_summary);
  const batch = asRecord(summary.profile_batch_advance);
  const smartJob = asRecord(summary.smart_search_profile_advance_job);
  return terminalSessionStatus(batch.status) || terminalSessionStatus(smartJob.status) || terminalSessionStatus(smartJob.advance_status);
}

export function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
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
export function looksLikeRetailer(item: any): boolean {
  const sf = (item && item.source_fields && typeof item.source_fields === "object") ? item.source_fields : {};
  const hay = `${item?.handle || ""} ${item?.display_name || ""} ${item?.why_fit || ""} ${sf.bio || sf.description || ""}`.toLowerCase();
  return /\bb\s*&\s*h\b|b\s*and\s*h|pro\s*audio|photo\s*video|camera\s*(store|house|shop|world|land)|rental|retailer|wholesale|distributor|旗舰店|专卖|经销/.test(hay);
}

// 三框·框3:从会话抽 new_creator(Apify+平台发现)项,带头像/用户名/平台。
export function discoveryItemsFromSession(session: VkpiKolSearchHistoryItem | null): VkpiKolRecallItem[] {
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

export function urlResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolUrlDeepCrawlResponse | null {
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

export function sessionStatusBanner(
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

export function HistoryStrip({
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

export function RecallMiniItem({
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

export function PlanPills({ plan }: { plan: Row }) {
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

export function ProfileInfoCard({ data, onOpen, apiToken }: { data: Row; onOpen?: () => void; apiToken?: string }) {
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

export function UrlSummary({
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
