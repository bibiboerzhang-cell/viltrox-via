import { useMemo, useState } from "react";
import { AlertTriangle, BadgeCheck, Database, Link2, Loader2, Search, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";

type PanelState = "idle" | "dryRunLoading" | "dryRunReady" | "executeLoading" | "executeReady" | "error";

function cleanText(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function displayText(value: unknown, fallback = "--"): string {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return cleanText(value) || fallback;
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return (
      cleanText(record.label) ||
      cleanText(record.description) ||
      cleanText(record.code) ||
      fallback
    );
  }
  return fallback;
}

function actionDescription(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const record = value as Record<string, unknown>;
  return cleanText(record.description);
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => displayText(item, "")).filter(Boolean) : [];
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function statusTone(result: VkpiKolUrlDeepCrawlResponse | null): string {
  if (!result) return "border-white/[0.07] bg-white/[0.025] text-slate-400";
  if (result.url_type === "profile") return "border-emerald-300/20 bg-emerald-400/[0.08] text-emerald-100";
  if (result.url_type === "video") return "border-amber-300/20 bg-amber-400/[0.08] text-amber-100";
  return "border-rose-300/20 bg-rose-500/[0.08] text-rose-100";
}

function urlTypeLabel(value: unknown): string {
  const text = displayText(value, "");
  if (text === "profile") return "Profile URL";
  if (text === "video") return "Video URL";
  if (text === "unknown") return "Unknown";
  return text || "--";
}

function ActionMessage({ result }: { result: VkpiKolUrlDeepCrawlResponse }) {
  if (result.url_type === "unknown") {
    return (
      <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-100">
        无法识别这个链接，请粘贴 YouTube / Instagram / TikTok 的 KOL 主页或视频 URL。
      </div>
    );
  }
  return null;
}

function FieldPills({ fields }: { fields: string[] }) {
  if (!fields.length) {
    return <span className="text-slate-600">--</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {fields.map((field) => (
        <span key={field} className="rounded-md border border-white/[0.07] bg-black/15 px-1.5 py-0.5 text-[10px] text-slate-300">
          {field}
        </span>
      ))}
    </div>
  );
}

function ProfileDataPreview({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data).filter(([, value]) => value !== null && value !== undefined && displayText(value, "") !== "");
  if (!rows.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-emerald-300/15 bg-emerald-400/[0.06] px-3 py-2">
      <div className="mb-1.5 text-[10px] uppercase tracking-wide text-emerald-200/80">抓取到的基础资料</div>
      <div className="grid gap-1 text-[11px] text-slate-300">
        {rows.slice(0, 8).map(([key, value]) => (
          <div key={key} className="grid grid-cols-[92px_minmax(0,1fr)] gap-2">
            <span className="text-slate-500">{key}</span>
            <span className="truncate text-slate-200">{displayText(value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function videoExecutionSucceeded(status: unknown): boolean {
  return ["queued", "already_queued", "already_analyzed"].includes(cleanText(status));
}

function VideoUrlActionPanel({
  result,
  videoFlow,
  videoMetadata,
  creatorIdentity,
  evidenceResult,
  enqueueResult,
  canExecute,
  isExecuting,
  onExecute,
}: {
  result: VkpiKolUrlDeepCrawlResponse;
  videoFlow: Record<string, unknown>;
  videoMetadata: Record<string, unknown>;
  creatorIdentity: Record<string, unknown>;
  evidenceResult: Record<string, unknown>;
  enqueueResult: Record<string, unknown>;
  canExecute: boolean;
  isExecuting: boolean;
  onExecute: () => void;
}) {
  const profileFlow = asRecord(result.profile_flow);
  const profileOperation = cleanText(profileFlow.operation);
  const rawVideoOperation = cleanText(videoFlow.operation);
  const operation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : rawVideoOperation || profileOperation;
  const creatorResolved = cleanText(videoFlow.creator_resolution_status) === "resolved" || Boolean(creatorIdentity.profile_url || creatorIdentity.handle || creatorIdentity.channel_id || result.handle || result.channel_id);
  const isKnownCreator = operation === "existing_creator_video_analysis" || Boolean(result.in_pool);
  const actionLabel = isKnownCreator ? "只分析此视频" : "建档并分析";
  const creatorName = displayText(creatorIdentity.display_name || creatorIdentity.handle || creatorIdentity.channel_id, "创作者未解析");
  const job = asRecord(enqueueResult.job);
  const cache = asRecord(enqueueResult.cache);

  return (
    <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
      <div className="rounded-lg border border-white/[0.07] bg-black/15 p-3">
        <div className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-500">
          <Video size={11} />
          Video URL 智能识别
        </div>
        <div className="grid gap-1.5 text-[11px] text-slate-300">
          <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
            <span className="text-slate-500">视频</span>
            <span className="truncate text-slate-200">{displayText(videoMetadata.title || result.video_id)}</span>
          </div>
          <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
            <span className="text-slate-500">创作者</span>
            <span className="truncate text-slate-200">{creatorName}</span>
          </div>
          <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
            <span className="text-slate-500">身份</span>
            <span className="truncate text-slate-200">{displayText(creatorIdentity.channel_id || creatorIdentity.handle || creatorIdentity.profile_url)}</span>
          </div>
          <div className="grid grid-cols-[76px_minmax(0,1fr)] gap-2">
            <span className="text-slate-500">动作</span>
            <span className="text-slate-200">{isKnownCreator ? "创作者已在库，当前视频入队分析" : "创作者不在库，先建档再分析当前视频"}</span>
          </div>
        </div>
      </div>

      <div className="rounded-lg border border-white/[0.07] bg-black/15 p-3">
        <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">确认执行</div>
        {creatorResolved ? (
          <div className="text-[11px] leading-relaxed text-slate-300">
            {isKnownCreator ? "不会抓账号或建档，只会创建/复用这条 evidence 并排入 final_v1。" : "会抓账号基础资料，新建最小 KOL 档案，再创建 evidence 并排入 final_v1。"}
          </div>
        ) : (
          <div className="rounded-md border border-amber-300/20 bg-amber-400/[0.08] px-2 py-1.5 text-[11px] text-amber-100">
            创作者未解析，不能建匿名档，也不会执行入队。
          </div>
        )}
        <button
          type="button"
          onClick={onExecute}
          disabled={!canExecute}
          className="mt-3 inline-flex min-h-[32px] items-center justify-center gap-1.5 rounded-md border border-amber-300/20 bg-amber-500/[0.14] px-3 text-[11px] text-amber-100 transition-colors hover:bg-amber-500/[0.22] disabled:cursor-not-allowed disabled:border-white/[0.08] disabled:bg-white/[0.04] disabled:text-slate-500"
          title={canExecute ? "确认后会执行对应 video URL 动作，任务会进入侧边栏任务看板" : "需要先完成 video URL dry-run，并解析到唯一可执行动作"}
        >
          {isExecuting ? <Loader2 size={12} className="animate-spin" /> : null}
          {isExecuting ? "处理中..." : actionLabel}
        </button>

        {result.execute ? (
          <div className="mt-3 grid gap-1 text-[10px] text-slate-500">
            <div>kol_pool_id: <span className="text-slate-300">{displayText(videoFlow.kol_pool_id || result.matched_kol_pool_id)}</span></div>
            <div>evidence_id: <span className="text-slate-300">{displayText(videoFlow.evidence_id || evidenceResult.evidence_id)}</span></div>
            <div>job_id: <span className="text-slate-300">{displayText(job.id || cache.id)}</span></div>
            <div>run_id: <span className="text-slate-300">{displayText(videoFlow.run_id)}</span></div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function UrlDeepCrawlPanel({ apiToken = "" }: { apiToken?: string }) {
  const [inputUrl, setInputUrl] = useState("");
  const [panelState, setPanelState] = useState<PanelState>("idle");
  const [result, setResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(null);
  const [error, setError] = useState("");

  const profileFlow = result?.profile_flow || {};
  const videoFlow = asRecord(result?.video_flow);
  const videoMetadata = asRecord(result?.video_metadata || videoFlow.video_metadata);
  const creatorIdentity = asRecord(result?.creator_identity || videoFlow.creator_identity);
  const evidenceResult = asRecord(videoFlow.evidence_result);
  const enqueueResult = asRecord(videoFlow.enqueue_result);
  const wouldCrawl = profileFlow.would_crawl || {};
  const writerDryRun = profileFlow.safe_writer_dry_run || {};
  const writeResult = asRecord(profileFlow.write_result);
  const profileData = asRecord(profileFlow.profile_data);
  const representativeVideoAnalysis = asRecord(profileFlow.representative_video_analysis);
  const representativeIncremental = asRecord(representativeVideoAnalysis.incremental);
  const fieldsToWrite = useMemo(() => asStringList(writerDryRun.fields_to_write), [writerDryRun.fields_to_write]);
  const fieldsWritten = useMemo(() => asStringList(writeResult.fields_written), [writeResult.fields_written]);
  const candidates = Array.isArray(result?.candidates) ? result?.candidates || [] : [];
  const nextActionLabel = displayText(result?.next_action);
  const nextActionDescription = actionDescription(result?.next_action);
  const isLoading = panelState === "dryRunLoading" || panelState === "executeLoading";
  const isExecuting = panelState === "executeLoading";
  const profileExecuteSucceeded = Boolean(result?.execute && result.url_type === "profile" && profileFlow.status === "ready");
  const videoExecuteSucceeded = Boolean(result?.execute && result.url_type === "video" && videoExecutionSucceeded(videoFlow.status));
  const executeSucceeded = profileExecuteSucceeded || videoExecuteSucceeded;
  const executeFailed = Boolean(result?.execute && !executeSucceeded && (profileFlow.status || videoFlow.status));
  const canSubmit = Boolean(apiToken && cleanText(inputUrl)) && !isLoading;
  const canExecuteProfile = Boolean(
    apiToken &&
    result &&
    !result.execute &&
    result.url_type === "profile" &&
    profileFlow.status === "dry_run_ready" &&
    candidates.length <= 1 &&
    !isLoading,
  );
  const videoStatus = cleanText(profileFlow.status || videoFlow.status);
  const profileOperation = cleanText(profileFlow.operation);
  const rawVideoOperation = cleanText(videoFlow.operation);
  const videoOperation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : rawVideoOperation || profileOperation;
  const videoCreatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(creatorIdentity.handle || creatorIdentity.channel_id || creatorIdentity.profile_url || result?.handle || result?.channel_id),
  );
  const canExecuteVideo = Boolean(
    apiToken &&
    result &&
    !result.execute &&
    result.url_type === "video" &&
    ["dry_run_ready", "ready_to_execute"].includes(videoStatus) &&
    videoCreatorResolved &&
    ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(videoOperation) &&
    !isLoading,
  );
  const canExecute = canExecuteProfile || canExecuteVideo;

  const runDryRun = async () => {
    const url = cleanText(inputUrl);
    if (!apiToken) {
      setPanelState("error");
      setError("未登录 / 无 token");
      setResult(null);
      return;
    }
    if (!url) {
      setPanelState("error");
      setError("URL 为空");
      setResult(null);
      return;
    }
    setPanelState("dryRunLoading");
    setError("");
    try {
      const response = await deepCrawlKolUrl(apiToken, url, false, { maxPosts: 3, mode: "auto" });
      setResult(response);
      setPanelState("dryRunReady");
    } catch (err) {
      setResult(null);
      setPanelState("error");
      setError(err instanceof Error ? err.message : "URL 深抓 dry-run 接口失败");
    }
  };

  const runExecute = async () => {
    const url = cleanText(result?.url?.input || inputUrl);
    if (!apiToken || !url || !canExecute) return;
    setPanelState("executeLoading");
    setError("");
    try {
      const executeMode = result?.url_type === "video" ? "video_deep" : "auto";
      const response = await deepCrawlKolUrl(apiToken, url, true, {
        maxPosts: typeof profileFlow.max_posts === "number" ? profileFlow.max_posts : 3,
        mode: executeMode,
        timeoutMs: 300000,
      });
      const responseVideoFlow = asRecord(response.video_flow);
      const responseSucceeded = response.url_type === "video"
        ? videoExecutionSucceeded(responseVideoFlow.status)
        : response.profile_flow?.status === "ready";
      setResult(response);
      setPanelState(responseSucceeded ? "executeReady" : "dryRunReady");
      if (!responseSucceeded) {
        setError(displayText(responseVideoFlow.message || responseVideoFlow.error || response.profile_flow?.message || response.profile_flow?.status, "URL 深抓执行失败"));
      }
    } catch (err) {
      setPanelState("dryRunReady");
      setError(err instanceof Error ? err.message : "URL 深抓 execute 接口失败");
    }
  };

  return (
    <section className="mb-4 rounded-lg border border-cyan-300/[0.12] bg-cyan-950/[0.08] p-3">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/[0.10] text-cyan-200">
              <Link2 size={14} />
            </span>
              <div>
                <h2 className="text-[13px] font-semibold text-white">URL 深抓入口</h2>
              <div className="mt-0.5 text-[10.5px] text-slate-500">先 dry-run 识别 · 确认后抓资料或分析视频</div>
              </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
          <span className="rounded-md border border-white/[0.07] px-2 py-1">Profile 资料</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">Video 智能分析</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">不直接调 Gemini</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">确认后入队</span>
        </div>
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
        <input
          value={inputUrl}
          onChange={(event) => setInputUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && canSubmit) void runDryRun();
          }}
          placeholder="粘贴 KOL 主页或视频 URL"
          className="min-h-[42px] rounded-md border border-white/[0.08] bg-black/20 px-3 py-2 text-[11px] text-slate-300 outline-none placeholder-slate-600 focus:border-cyan-300/40"
        />
        <button
          type="button"
          onClick={() => void runDryRun()}
          disabled={!canSubmit}
          className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-md border border-cyan-300/20 bg-cyan-500/[0.16] px-3 text-[11px] font-medium text-cyan-100 transition-colors hover:bg-cyan-500/[0.24] disabled:cursor-not-allowed disabled:opacity-55"
        >
          {isLoading ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
          识别 URL
        </button>
      </div>

      {error ? (
        <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-200">{error}</div>
      ) : null}

      {result ? (
        <div className={`mt-3 rounded-lg border px-3 py-3 ${statusTone(result)}`}>
          <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] bg-black/15 px-2 py-1">
                  {result.url_type === "video" ? <Video size={11} /> : result.url_type === "profile" ? <BadgeCheck size={11} /> : <AlertTriangle size={11} />}
                  {urlTypeLabel(result.url_type)}
                </span>
                <span className="rounded-md border border-white/[0.08] bg-black/15 px-2 py-1">{displayText(result.platform)}</span>
                <span className="rounded-md border border-white/[0.08] bg-black/15 px-2 py-1">
                  {displayText(result.handle || result.channel_id || result.video_id)}
                </span>
                <span className="rounded-md border border-white/[0.08] bg-black/15 px-2 py-1">
                  {result.in_pool ? `已在库 #${result.matched_kol_pool_id || "--"}` : "未命中库内 KOL"}
                </span>
              </div>
              <div className="mt-2 truncate text-[10.5px] text-slate-500">
                normalized: {result.url?.normalized || "--"}
              </div>
            </div>
            <div className="shrink-0 text-[10px] text-slate-500">
              next: <span className="text-slate-300">{nextActionLabel}</span>
              {nextActionDescription ? <div className="mt-1 max-w-[240px] text-right leading-snug text-slate-600">{nextActionDescription}</div> : null}
            </div>
          </div>

          <ActionMessage result={result} />

          {executeSucceeded ? (
            <div className="mt-3 rounded-lg border border-emerald-300/20 bg-emerald-400/[0.10] px-3 py-2 text-[11px] text-emerald-100">
              {result.url_type === "video" ? (
                <>
                  视频动作已提交：{displayText(videoFlow.status)}。kol_pool_id {displayText(videoFlow.kol_pool_id || result.matched_kol_pool_id)} · evidence_id {displayText(videoFlow.evidence_id || evidenceResult.evidence_id)}。任务会出现在侧边栏任务看板。V6 Fit 未触碰：{String(Boolean(videoFlow.viltrox_fit_score_untouched))}
                </>
              ) : (
                <>
                  基础资料抓取完成，已通过安全 writer 写入。
                  {representativeVideoAnalysis.enabled ? ` 代表视频入队：${displayText(representativeVideoAnalysis.queued, "0")} 条。` : " 未启用代表视频分析。"}
                  V6 Fit 未触碰：{String(Boolean(profileFlow.viltrox_fit_score_untouched || writeResult.viltrox_fit_score_untouched))}
                </>
              )}
            </div>
          ) : null}

          {executeFailed ? (
            <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-400/[0.08] px-3 py-2 text-[11px] text-amber-100">
              执行未完成：{displayText(videoFlow.message || videoFlow.error || profileFlow.message || profileFlow.status || videoFlow.status)}
            </div>
          ) : null}

          {candidates.length > 1 ? (
            <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-400/[0.08] px-3 py-2 text-[11px] text-amber-100">
              命中多个候选，需要人工选择后才能执行。
            </div>
          ) : null}

          {result.url_type === "profile" ? (
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <div className="rounded-lg border border-white/[0.07] bg-black/15 p-3">
                <div className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-500">
                  <Database size={11} />
                  会抓什么
                </div>
                <div className="grid gap-1.5 text-[11px] text-slate-300">
                  <div>crawler: {displayText(wouldCrawl.crawler)}</div>
                  <div>target: {displayText(wouldCrawl.target || profileFlow.target)}</div>
                  <div>uses_decodo: {String(Boolean(wouldCrawl.uses_decodo))}</div>
                  <div>uses_gemini: {String(Boolean(wouldCrawl.uses_gemini))}</div>
                  <div>uses_worker: {String(Boolean(wouldCrawl.uses_worker))}</div>
                </div>
              </div>
              <div className="rounded-lg border border-white/[0.07] bg-black/15 p-3">
                <div className="mb-2 text-[10px] uppercase tracking-wide text-slate-500">会写哪些字段</div>
                <FieldPills fields={fieldsWritten.length ? fieldsWritten : fieldsToWrite} />
                <button
                  type="button"
                  onClick={() => void runExecute()}
                  disabled={!canExecuteProfile}
                  className="mt-3 inline-flex min-h-[32px] items-center justify-center gap-1.5 rounded-md border border-emerald-300/20 bg-emerald-500/[0.14] px-3 text-[11px] text-emerald-100 transition-colors hover:bg-emerald-500/[0.22] disabled:cursor-not-allowed disabled:border-white/[0.08] disabled:bg-white/[0.04] disabled:text-slate-500"
                  title={canExecuteProfile ? "确认后会抓取 profile 基础资料，并最多排入 1 条代表视频 final_v1" : "需要先完成 profile URL dry-run，且不能有多个候选"}
                >
                  {isExecuting ? <Loader2 size={12} className="animate-spin" /> : null}
                  确认抓取并分析代表视频
                </button>
                {representativeVideoAnalysis.status ? (
                  <div className="mt-2 text-[10px] text-slate-500">
                    representative_video_analysis: {displayText(representativeVideoAnalysis.status)}
                    {representativeVideoAnalysis.queued !== undefined ? ` · queued ${displayText(representativeVideoAnalysis.queued)}` : ""}
                    {representativeIncremental.last_video_at ? ` · since ${displayText(representativeIncremental.last_video_at)}` : ""}
                  </div>
                ) : null}
                {writeResult.viltrox_fit_score_changed_ids ? (
                  <div className="mt-2 text-[10px] text-slate-500">
                    viltrox_fit_score_changed_ids: {displayText(writeResult.viltrox_fit_score_changed_ids)}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}
          {result.url_type === "video" ? (
            <VideoUrlActionPanel
              result={result}
              videoFlow={videoFlow}
              videoMetadata={videoMetadata}
              creatorIdentity={creatorIdentity}
              evidenceResult={evidenceResult}
              enqueueResult={enqueueResult}
              canExecute={canExecuteVideo}
              isExecuting={isExecuting}
              onExecute={() => void runExecute()}
            />
          ) : null}
          <ProfileDataPreview data={profileData} />
        </div>
      ) : null}
    </section>
  );
}
