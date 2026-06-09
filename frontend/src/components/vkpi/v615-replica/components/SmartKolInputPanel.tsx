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
  return ["queued", "already_queued", "already_analyzed"].includes(cleanText(status));
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

function recallResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolRecallResponse {
  const creator: VkpiKolRecallItem[] = [];
  const reviewer: VkpiKolRecallItem[] = [];
  sessionItems(session).forEach((item) => {
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
      source_fields: payload,
    } satisfies VkpiKolRecallItem;
    if (bucket === "reviewer") reviewer.push(row);
    else creator.push(row);
  });
  const summary = asRecord(session.result_summary);
  const diagnostics = asRecord(summary.diagnostics);
  return {
    method: "search_session_history",
    query: { query_text: display(summary.query || session.query_text, "") },
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

function urlResultFromSession(session: VkpiKolSearchHistoryItem): VkpiKolUrlDeepCrawlResponse | null {
  const item = sessionItems(session).find((entry) => cleanText(entry.item_type).startsWith("url_")) || sessionItems(session)[0];
  if (!item) return null;
  const payload = asRecord(item.payload);
  const videoFlow = asRecord(payload.video_flow);
  const profileFlow = asRecord(payload.profile_flow);
  const urlType = cleanText(payload.url_type || session.query_type).includes("video") ? "video" : cleanText(payload.url_type || session.query_type).includes("profile") ? "profile" : "unknown";
  return {
    method: "search_session_history",
    execute: false,
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
    next_action: "history_restore",
    profile_flow: profileFlow,
    video_flow: videoFlow,
    creator_identity: asRecord(payload.creator_identity),
    video_metadata: asRecord(payload.video_metadata),
    search_session: { id: session.id, session_id: session.id, status: session.status },
    safety: { viltrox_fit_score_untouched: Boolean(payload.viltrox_fit_score_untouched) },
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

function RecallMiniItem({
  item,
  index,
  onOpen,
}: {
  item: VkpiKolRecallItem;
  index: number;
  onOpen?: (item: VkpiKolRecallItem) => void;
}) {
  const avatar = proxiedImageUrl(item.avatar_url);
  const name = display(item.handle || item.display_name || `KOL #${item.kol_pool_id}`);
  const followers = numberLabel(item.followers);
  return (
    <button
      type="button"
      onClick={() => onOpen?.(item)}
      className="flex min-w-0 items-center gap-2 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5 text-left transition-colors hover:border-cyan-300/22 hover:bg-cyan-400/[0.045] focus:outline-none focus:ring-1 focus:ring-cyan-300/30"
      title="打开 KOL 详情"
    >
      <span className="shrink-0 rounded border border-white/[0.06] bg-white/[0.03] px-1 py-0.5 text-[8.5px] tabular-nums text-slate-500">
        #{index}
      </span>
      <span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-md border border-white/[0.08] bg-white/[0.04] text-[10px] text-slate-300">
        {avatar ? <img src={avatar} alt="" className="h-full w-full object-cover" referrerPolicy="no-referrer" /> : name.slice(0, 1).toUpperCase()}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[11px] font-medium text-slate-100">{name}</span>
        <span className="block truncate text-[9.5px] text-slate-500">
          {display(item.platform, "unknown")} · {item.type_label || item.profile_type || "profile"}{followers ? ` · ${followers}` : ""}
        </span>
      </span>
      <span className="shrink-0 rounded-md border border-violet-300/15 px-1.5 py-0.5 text-[9.5px] text-violet-100">
        {Number(item.recall_rank_score ?? item.vector_score ?? 0).toFixed(2)}
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

function UrlSummary({
  result,
  canExecute,
  isExecuting,
  onExecute,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
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
  const platform = cleanText(result.platform).toLowerCase();
  const isVideo = result.url_type === "video";
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
  const executeDone = result.execute && (
    isVideo ? videoExecutionDone(videoFlow.status) : cleanText(profileFlow.status) === "ready"
  );
  const actionLabel = isVideo
    ? knownCreator ? "只分析此视频" : "建档并分析"
    : "抓基础资料";
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
          <div className="mt-2 grid gap-1 text-[11px] text-slate-400 sm:grid-cols-2">
            <div className="truncate">对象: <span className="text-slate-200">{display(metadata.title || creator.display_name || creator.handle || result.handle || result.video_id)}</span></div>
            <div className="truncate">身份: <span className="text-slate-200">{display(creator.channel_id || creator.handle || result.channel_id || result.handle)}</span></div>
            <div className="truncate sm:col-span-2">normalized: <span className="text-slate-500">{display(result.url?.normalized)}</span></div>
          </div>
        </div>
        <div className="shrink-0">
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
        </div>
      </div>
      {disabledReason ? (
        <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1.5 text-[10.5px] text-amber-100">
          {disabledReason}
        </div>
      ) : null}
      {executeDone ? (
        <div className="mt-2 rounded-md border border-emerald-300/20 bg-emerald-400/[0.10] px-2 py-1.5 text-[10.5px] text-emerald-100">
          执行完成: {isVideo
            ? `kol_pool_id ${display(videoFlow.kol_pool_id || result.matched_kol_pool_id)} · evidence_id ${display(videoFlow.evidence_id || evidence.evidence_id)} · job_id ${display(enqueueJob.id || enqueue.id)}`
            : `kol_pool_id ${display(profileFlow.kol_pool_id)} · V6 Fit 未触碰 ${display(profileFlow.viltrox_fit_score_untouched)}`}
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
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");

  const inferredMode = useMemo(() => detectMode(input), [input]);
  const isBusy = state === "loading" || state === "executing";
  const profileFlow = asRecord(urlResult?.profile_flow);
  const videoFlow = asRecord(urlResult?.video_flow);
  const videoCreator = asRecord(urlResult?.creator_identity || videoFlow.creator_identity);
  const videoStatus = cleanText(profileFlow.status || videoFlow.status);
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
    !urlResult.execute &&
    !isBusy &&
    (
      (urlResult.url_type === "profile" && cleanText(profileFlow.status) === "dry_run_ready") ||
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

  const run = async () => {
    const query = cleanText(input);
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
    try {
      const response = await smartKolSearch(apiToken, query, {
        mode: "auto",
        maxPosts: 3,
        candidateLimit: 50,
        limit: 10,
        creatorQuota: 7,
        reviewerQuota: 3,
        createSession: true,
        timeoutMs: 60000,
      });
      const responseMode = cleanText(response.mode);
      if (responseMode === "url" || cleanText(response.query_type).startsWith("url_")) {
        setMode("url");
        setUrlResult(response.result as VkpiKolUrlDeepCrawlResponse);
      } else {
        setMode("text");
        setRecallResult(response.result as VkpiKolRecallResponse);
      }
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "智能入口请求失败");
    }
  };

  const executeUrlAction = async () => {
    const query = cleanText(urlResult?.url?.input || input);
    if (!apiToken || !query || !urlCanExecute || !urlResult) return;
    setState("executing");
    setError("");
    try {
      const executeMode = urlResult.url_type === "video" ? "video_deep" : "auto";
      const sessionId = sessionIdFrom(urlResult.search_session);
      const response = await deepCrawlKolUrl(apiToken, query, true, {
        maxPosts: typeof profileFlow.max_posts === "number" ? profileFlow.max_posts : 3,
        mode: executeMode,
        sessionId,
        createSession: !sessionId,
        source: "smart_kol_input",
        timeoutMs: 300000,
      });
      setUrlResult(response);
      setState("ready");
      void refreshHistory();
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "URL 执行失败");
    }
  };

  const queueTextAdvance = async () => {
    const query = cleanText(input);
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
        timeoutMs: 300000,
      });
      setAdvanceResult(response);
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
          canExecute={urlCanExecute}
          isExecuting={state === "executing"}
          onExecute={() => void executeUrlAction()}
        />
      ) : null}

      {mode === "text" && recallResult ? (
        <div className="mt-3 rounded-lg border border-violet-300/15 bg-violet-950/[0.10] p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="text-[11px] font-medium text-violet-100">LLM 查找结果</div>
            <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-500">
              <span className="rounded-md border border-white/[0.07] px-2 py-1">候选 {display(recallResult.diagnostics?.candidate_count)}</span>
              <span className="rounded-md border border-white/[0.07] px-2 py-1">创作者 {display(recallResult.diagnostics?.creator_returned)}</span>
              <span className="rounded-md border border-white/[0.07] px-2 py-1">测评号 {display(recallResult.diagnostics?.reviewer_returned)}</span>
            </div>
          </div>
          {Object.keys(llmPlan).length ? <PlanPills plan={llmPlan} /> : null}
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {recallItems.map((item, index) => (
              <RecallMiniItem
                key={`${item.bucket}-${item.kol_pool_id || item.handle || index}`}
                item={item}
                index={index + 1}
                onOpen={onOpenRecallItem}
              />
            ))}
          </div>
          {!recallItems.length ? (
            <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">暂无召回结果</div>
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void queueTextAdvance()}
              disabled={state === "executing" || !apiToken || !cleanText(input)}
              className="inline-flex min-h-[32px] items-center justify-center gap-1.5 rounded-md border border-emerald-300/18 bg-emerald-500/[0.12] px-3 text-[10.5px] font-medium text-emerald-100 transition-colors hover:bg-emerald-500/[0.20] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {state === "executing" ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
              后台深度查找
            </button>
            <span className="text-[10px] text-slate-600">15+15 候选 · 新发现 · 逐个补档 · V6 Fit 不触碰</span>
          </div>
          {advanceResult ? (
            <div className="mt-2 rounded-md border border-emerald-300/18 bg-emerald-400/[0.08] px-2.5 py-2 text-[10.5px] text-emerald-100">
              已入队: {display(advanceResult.status)} · 看侧边栏任务进度。V6 Fit 未触碰: {display(advanceResult.viltrox_fit_score_untouched)}
            </div>
          ) : null}
        </div>
      ) : null}

    </section>
  );
}
