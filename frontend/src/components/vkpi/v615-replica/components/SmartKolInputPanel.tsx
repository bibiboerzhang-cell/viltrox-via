import { useMemo, useState } from "react";
import { AlertTriangle, BadgeCheck, Database, Link2, Loader2, Search, Sparkles, UserPlus, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  recallKolProfiles,
  type VkpiKolRecallItem,
  type VkpiKolRecallResponse,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";
import { proxiedImageUrl } from "../../shared/mediaProxy";

type Mode = "idle" | "url" | "text";
type State = "idle" | "loading" | "ready" | "executing" | "error";
type Row = Record<string, unknown>;

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
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? "url" : "text";
  } catch {
    return "text";
  }
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

function RecallMiniItem({ item }: { item: VkpiKolRecallItem }) {
  const avatar = proxiedImageUrl(item.avatar_url);
  const name = display(item.handle || item.display_name || `KOL #${item.kol_pool_id}`);
  const followers = numberLabel(item.followers);
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-md border border-white/[0.06] bg-black/20 px-2 py-1.5">
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
  const operation = cleanText(videoFlow.operation || profileFlow.operation);
  const knownCreator = Boolean(result.in_pool || operation === "existing_creator_video_analysis");
  const creatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(creator.handle || creator.channel_id || creator.profile_url),
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

export function SmartKolInputPanel({ apiToken = "" }: { apiToken?: string }) {
  const [input, setInput] = useState("");
  const [state, setState] = useState<State>("idle");
  const [mode, setMode] = useState<Mode>("idle");
  const [urlResult, setUrlResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(null);
  const [recallResult, setRecallResult] = useState<VkpiKolRecallResponse | null>(null);
  const [error, setError] = useState("");

  const inferredMode = useMemo(() => detectMode(input), [input]);
  const isBusy = state === "loading" || state === "executing";
  const profileFlow = asRecord(urlResult?.profile_flow);
  const videoFlow = asRecord(urlResult?.video_flow);
  const urlCanExecute = Boolean(
    apiToken &&
    urlResult &&
    !urlResult.execute &&
    !isBusy &&
    (
      (urlResult.url_type === "profile" && cleanText(profileFlow.status) === "dry_run_ready") ||
      (urlResult.url_type === "video" && cleanText(profileFlow.status) === "dry_run_ready" && ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(cleanText(videoFlow.operation || profileFlow.operation)))
    )
  );
  const recallItems = recallTopItems(recallResult);

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
    try {
      if (nextMode === "url") {
        const response = await deepCrawlKolUrl(apiToken, query, false, { maxPosts: 3, mode: "auto" });
        setUrlResult(response);
      } else {
        const response = await recallKolProfiles(apiToken, {
          queryText: query,
          candidateLimit: 50,
          limit: 10,
          creatorQuota: 7,
          reviewerQuota: 3,
          ratioPolicy: "soft",
          mixedPolicy: "dominant",
          dedupe: true,
        });
        setRecallResult(response);
      }
      setState("ready");
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
      const response = await deepCrawlKolUrl(apiToken, query, true, {
        maxPosts: typeof profileFlow.max_posts === "number" ? profileFlow.max_posts : 3,
        mode: executeMode,
        timeoutMs: 300000,
      });
      setUrlResult(response);
      setState("ready");
    } catch (err) {
      setState("ready");
      setError(err instanceof Error ? err.message : "URL 执行失败");
    }
  };

  return (
    <section
      data-testid="smart-kol-input-panel"
      className="mb-4 rounded-lg border border-cyan-300/[0.14] bg-gradient-to-br from-cyan-950/[0.10] via-violet-950/[0.08] to-black/10 p-3"
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/[0.10] text-cyan-200">
              <Sparkles size={14} />
            </span>
            <div>
              <h2 className="text-[13px] font-semibold text-white">智能输入入口</h2>
              <div className="mt-0.5 text-[10.5px] text-slate-500">URL 自动分流 · 纯文字语义召回 · 旧入口保留回退</div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
          <span className="rounded-md border border-white/[0.07] px-2 py-1">Video URL</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">Profile URL</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">文字召回</span>
          <span className="rounded-md border border-emerald-300/15 bg-emerald-400/[0.06] px-2 py-1 text-emerald-100">V6 Fit 不触碰</span>
        </div>
      </div>

      <form
        className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto]"
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
          placeholder="粘贴 KOL 主页/视频 URL，或输入产品需求文本"
          className="min-h-[42px] rounded-md border border-white/[0.08] bg-black/20 px-3 py-2 text-[11px] text-slate-300 outline-none placeholder-slate-600 focus:border-cyan-300/40"
        />
        <button
          data-testid="smart-kol-run"
          type="submit"
          disabled={isBusy || !apiToken || !cleanText(input)}
          className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.16] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.24] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {isBusy ? <Loader2 size={13} className="animate-spin" /> : inferredMode === "url" ? <Link2 size={13} /> : <Search size={13} />}
          {inferredMode === "url" ? "识别 URL" : "智能召回"}
        </button>
      </form>

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
            <div className="text-[11px] font-medium text-violet-100">语义召回结果</div>
            <div className="flex flex-wrap gap-1.5 text-[10px] text-slate-500">
              <span className="rounded-md border border-white/[0.07] px-2 py-1">候选 {display(recallResult.diagnostics?.candidate_count)}</span>
              <span className="rounded-md border border-white/[0.07] px-2 py-1">创作者 {display(recallResult.diagnostics?.creator_returned)}</span>
              <span className="rounded-md border border-white/[0.07] px-2 py-1">测评号 {display(recallResult.diagnostics?.reviewer_returned)}</span>
            </div>
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {recallItems.map((item) => (
              <RecallMiniItem key={`${item.bucket}-${item.kol_pool_id}`} item={item} />
            ))}
          </div>
          {!recallItems.length ? (
            <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-4 text-center text-[11px] text-slate-500">暂无召回结果</div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-2 flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-600">
        <AlertTriangle size={11} className="mt-0.5 shrink-0 text-slate-600" />
        新入口先并存验证；下面旧 URL 深抓入口和产品召回结果仍保留回退。
      </div>
    </section>
  );
}
