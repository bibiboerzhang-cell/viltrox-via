import { useMemo, useState } from "react";
import { AlertTriangle, BadgeCheck, Database, Link2, Loader2, Search, Video } from "lucide-react";

import {
  deepCrawlKolUrl,
  type VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";

type PanelState = "idle" | "dryRunLoading" | "dryRunReady" | "error";

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
  if (result.url_type === "video") {
    return (
      <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-400/[0.08] px-3 py-2 text-[11px] text-amber-100">
        视频深度分析即将推出，本次不触发 Worker。
      </div>
    );
  }
  if (result.url_type === "unknown") {
    return (
      <div className="mt-3 rounded-lg border border-rose-300/20 bg-rose-500/[0.08] px-3 py-2 text-[11px] text-rose-100">
        无法识别这个链接，请粘贴 YouTube / Instagram / TikTok 的 KOL 主页 URL。
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

export function UrlDeepCrawlPanel({ apiToken = "" }: { apiToken?: string }) {
  const [inputUrl, setInputUrl] = useState("");
  const [panelState, setPanelState] = useState<PanelState>("idle");
  const [result, setResult] = useState<VkpiKolUrlDeepCrawlResponse | null>(null);
  const [error, setError] = useState("");

  const profileFlow = result?.profile_flow || {};
  const wouldCrawl = profileFlow.would_crawl || {};
  const writerDryRun = profileFlow.safe_writer_dry_run || {};
  const fieldsToWrite = useMemo(() => asStringList(writerDryRun.fields_to_write), [writerDryRun.fields_to_write]);
  const candidates = Array.isArray(result?.candidates) ? result?.candidates || [] : [];
  const nextActionLabel = displayText(result?.next_action);
  const nextActionDescription = actionDescription(result?.next_action);
  const isLoading = panelState === "dryRunLoading";
  const canSubmit = Boolean(apiToken && cleanText(inputUrl)) && !isLoading;

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
      const response = await deepCrawlKolUrl(apiToken, url, false, { maxPosts: 3, mode: "profile_only" });
      setResult(response);
      setPanelState("dryRunReady");
    } catch (err) {
      setResult(null);
      setPanelState("error");
      setError(err instanceof Error ? err.message : "URL 深抓 dry-run 接口失败");
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
              <div className="mt-0.5 text-[10.5px] text-slate-500">dry-run 识别 · 本刀不执行抓取写入</div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
          <span className="rounded-md border border-white/[0.07] px-2 py-1">Profile 资料</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">不触发 Gemini</span>
          <span className="rounded-md border border-white/[0.07] px-2 py-1">不触发 Worker</span>
        </div>
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto]">
        <input
          value={inputUrl}
          onChange={(event) => setInputUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && canSubmit) void runDryRun();
          }}
          placeholder="粘贴 KOL 主页 URL 深度抓取"
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
                <FieldPills fields={fieldsToWrite} />
                <button
                  type="button"
                  disabled
                  className="mt-3 inline-flex min-h-[32px] items-center justify-center rounded-md border border-white/[0.08] bg-white/[0.04] px-3 text-[11px] text-slate-500 disabled:cursor-not-allowed"
                  title="刀B接 execute=true"
                >
                  确认抓取基础资料
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
