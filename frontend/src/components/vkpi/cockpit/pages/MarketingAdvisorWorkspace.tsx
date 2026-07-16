import React from "react";
import { Check, Pause, Play, Plus, SendHorizontal, X } from "lucide-react";
import {
  confirmAdvisorMemoryCandidate,
  createAdvisorMemoryCandidate,
  createAdvisorThread,
  getAdvisorMemory,
  getAdvisorReadiness,
  listAdvisorMessages,
  listAdvisorThreads,
  postAdvisorMessageStream,
  rejectAdvisorMemoryCandidate,
  updateAdvisorMemoryFact,
  updateAdvisorMemorySettings,
  type AdvisorMemorySnapshot,
  type AdvisorMessage,
  type AdvisorReadiness,
  type AdvisorThread,
} from "../../../../services/vkpi/marketing-advisor-api";
import { formatLocal } from "../../lib/timeLocal";
import { humanizeLlmReason, llmErrorValue } from "../llmReasonCopy";
import { EmptyLine, LoadingLine, PendingCard } from "./MarketVoicePage.modules";

function errorText(error: unknown): string {
  return humanizeLlmReason(llmErrorValue(error), "请求失败，请稍后重试。").message;
}

function requestId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  } catch {
    // Fallback below keeps retry idempotency scoped to this one browser turn.
  }
  return `advisor-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function readinessLabel(readiness: AdvisorReadiness | null): { text: string; cls: string } {
  if (!readiness) return { text: "就绪状态不可用", cls: "border-crit bg-crit-soft text-crit" };
  if (readiness.provider_ready && readiness.persistence_ready) {
    return { text: "模型与持久化已就绪", cls: "border-good bg-good-soft text-good" };
  }
  const aiOffReady = readiness.ai_off_path_ready
    ?? (readiness.persistence_ready && readiness.knowledge_bridge_ready !== false);
  if (aiOffReady) return { text: "会话与记忆可用 · 外部模型关闭", cls: "border-warn bg-warn-soft text-warn" };
  if (readiness.persistence_ready) return { text: "会话可留存 · 模型降级", cls: "border-warn bg-warn-soft text-warn" };
  if (readiness.provider_ready) return { text: "模型已就绪 · 会话未就绪", cls: "border-crit bg-crit-soft text-crit" };
  return { text: "顾问未就绪", cls: "border-crit bg-crit-soft text-crit" };
}

function messageStatusLabel(message: AdvisorMessage): string {
  if (message.status === "degraded") return "诚实降级";
  if (message.status === "ready") return "已留存";
  if (message.status === "pending") return "处理中";
  if (message.status === "failed") return "失败";
  return message.status || "状态未知";
}

type PendingTurn = {
  threadUid: string;
  content: string;
  requestId: string;
  allowExternalAi: boolean;
};

type AdvisorTurnStage = "submitting" | "accepted" | "persisted" | "degraded" | "failed";

type AdvisorTurnProgressState = {
  threadUid: string;
  stage: AdvisorTurnStage;
  accepted: boolean;
  transport?: string;
  reason?: string;
};

function AdvisorTurnProgress({ progress }: { progress: AdvisorTurnProgressState }) {
  const terminal = progress.stage === "persisted" || progress.stage === "degraded" || progress.stage === "failed";
  const steps = [
    {
      label: "请求准备",
      state: progress.stage === "submitting" ? "active" : "ready",
    },
    {
      label: "服务端接收",
      state: progress.accepted ? "ready" : progress.stage === "failed" ? "failed" : "pending",
    },
    {
      label: "私有检索 / 模型路径",
      state: progress.stage === "accepted"
        ? "active"
        : progress.stage === "degraded"
          ? "warn"
          : progress.stage === "persisted"
            ? "ready"
            : progress.stage === "failed" && progress.accepted
              ? "failed"
              : "pending",
    },
    {
      label: "会话留存",
      state: progress.stage === "persisted" || progress.stage === "degraded"
        ? "ready"
        : progress.stage === "failed"
          ? "failed"
          : "pending",
    },
  ];
  const summary = progress.stage === "submitting"
    ? "正在提交幂等请求…"
    : progress.stage === "accepted"
      ? `服务端已接收 · ${progress.transport || "staged_sse_v1"} · 非 token 流`
      : progress.stage === "persisted"
        ? "分析结果已写入当前员工的持久会话"
        : progress.stage === "degraded"
          ? "本轮已持久化诚实降级结果；未冒充外部模型结论"
          : progress.reason || "本轮未形成可用结果，可使用相同请求 ID 安全重试";

  return (
    <div
      className={`mt-3 rounded-lg border px-3 py-2 ${progress.stage === "failed" ? "border-crit bg-crit-soft" : progress.stage === "degraded" ? "border-warn bg-warn-soft" : "border-line bg-panel"}`}
      role="status"
      aria-live="polite"
      aria-label="营销顾问分析进度"
    >
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {steps.map((step) => (
          <div
            key={step.label}
            className={`rounded-md border px-2 py-1 text-center text-[9.5px] ${step.state === "ready" ? "border-good bg-good-soft text-good" : step.state === "active" ? "border-accent bg-accent-soft text-accent" : step.state === "warn" ? "border-warn bg-warn-soft text-warn" : step.state === "failed" ? "border-crit bg-crit-soft text-crit" : "border-line text-muted"}`}
          >
            {step.label}
          </div>
        ))}
      </div>
      <div className={`mt-1.5 text-[9.5px] leading-4 ${progress.stage === "failed" ? "text-crit" : progress.stage === "degraded" ? "text-warn" : "text-muted"}`}>
        {summary}{!terminal && progress.accepted ? "；最终状态以服务端落库结果为准。" : ""}
      </div>
    </div>
  );
}

function MarketingAdvisorSession({ apiToken }: { apiToken: string }) {
  const [readiness, setReadiness] = React.useState<AdvisorReadiness | null>(null);
  const [threads, setThreads] = React.useState<AdvisorThread[]>([]);
  const [threadUid, setThreadUid] = React.useState("");
  const [messages, setMessages] = React.useState<AdvisorMessage[]>([]);
  const [input, setInput] = React.useState("");
  const [allowExternalAi, setAllowExternalAi] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [messagesLoading, setMessagesLoading] = React.useState(false);
  const [booting, setBooting] = React.useState(false);
  const [error, setError] = React.useState("");
  const [turnProgress, setTurnProgress] = React.useState<AdvisorTurnProgressState | null>(null);
  const busyRef = React.useRef(false);
  const mountedRef = React.useRef(true);
  const selectedThreadRef = React.useRef("");
  const pendingTurnRef = React.useRef<PendingTurn | null>(null);

  React.useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const selectThread = React.useCallback((nextUid: string) => {
    selectedThreadRef.current = nextUid;
    setThreadUid(nextUid);
    setMessages([]);
    setError("");
    setTurnProgress((current) => current?.threadUid === nextUid ? current : null);
  }, []);

  const refreshThreads = React.useCallback(async () => {
    const rows = await listAdvisorThreads(apiToken);
    if (mountedRef.current) setThreads(rows);
    return rows;
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setBooting(true);
    setError("");
    Promise.allSettled([getAdvisorReadiness(apiToken), listAdvisorThreads(apiToken)])
      .then(([readyResult, threadsResult]) => {
        if (!alive) return;
        if (readyResult.status === "fulfilled") setReadiness(readyResult.value);
        if (threadsResult.status === "fulfilled") {
          setThreads(threadsResult.value);
          selectThread(threadsResult.value[0]?.thread_uid || "");
        }
        const failure = readyResult.status === "rejected"
          ? readyResult.reason
          : threadsResult.status === "rejected"
            ? threadsResult.reason
            : null;
        if (failure) setError(errorText(failure));
      })
      .finally(() => {
        if (alive) setBooting(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, selectThread]);

  React.useEffect(() => {
    if (!apiToken || !threadUid) {
      setMessages([]);
      return;
    }
    let alive = true;
    setMessagesLoading(true);
    listAdvisorMessages(apiToken, threadUid)
      .then((rows) => {
        if (alive) setMessages(rows);
      })
      .catch((err) => {
        if (alive) setError(errorText(err));
      })
      .finally(() => {
        if (alive) setMessagesLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken, threadUid]);

  const startThread = React.useCallback(async (title = "新营销顾问会话") => {
    const created = await createAdvisorThread(apiToken, title.slice(0, 120));
    if (!mountedRef.current) return created.thread_uid;
    setThreads((rows) => [created, ...rows.filter((item) => item.thread_uid !== created.thread_uid)]);
    selectThread(created.thread_uid);
    return created.thread_uid;
  }, [apiToken, selectThread]);

  const createEmptyThread = React.useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    setLoading(true);
    setError("");
    try {
      await startThread();
    } catch (err) {
      if (mountedRef.current) setError(errorText(err));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setLoading(false);
    }
  }, [startThread]);

  const send = React.useCallback(async () => {
    const content = input.trim();
    if (!apiToken || !content || busyRef.current) return;
    busyRef.current = true;
    setLoading(true);
    setError("");
    let activeUid = threadUid;
    let serverAccepted = false;
    try {
      activeUid = threadUid || await startThread(content.slice(0, 60));
      const prior = pendingTurnRef.current;
      const pending = prior
        && prior.threadUid === activeUid
        && prior.content === content
        && prior.allowExternalAi === allowExternalAi
        ? prior
        : { threadUid: activeUid, content, requestId: requestId(), allowExternalAi };
      pendingTurnRef.current = pending;
      if (mountedRef.current && selectedThreadRef.current === activeUid) {
        setTurnProgress({ threadUid: activeUid, stage: "submitting", accepted: false });
      }
      const response = await postAdvisorMessageStream(
        apiToken,
        activeUid,
        content,
        pending.requestId,
        pending.allowExternalAi,
        (event) => {
          if (!mountedRef.current || selectedThreadRef.current !== activeUid) return;
          if (event.type === "accepted") {
            serverAccepted = true;
            setTurnProgress({
              threadUid: activeUid,
              stage: "accepted",
              accepted: true,
              transport: String(event.payload.transport || "staged_sse_v1"),
            });
          }
          if (event.type === "error") {
            setTurnProgress({
              threadUid: activeUid,
              stage: "failed",
              accepted: serverAccepted,
              reason: errorText(new Error(String(event.payload.code || "advisor_stream_failed"))),
            });
          }
        },
      );
      const returned = Array.isArray(response.messages) ? response.messages : [];
      if (selectedThreadRef.current === activeUid && returned.length > 0) {
        setMessages((current) => {
          const byUid = new Map(current.map((item) => [item.message_uid, item]));
          returned.forEach((item) => byUid.set(item.message_uid, item));
          return Array.from(byUid.values()).sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
        });
      } else if (selectedThreadRef.current === activeUid) {
        const rows = await listAdvisorMessages(apiToken, activeUid);
        if (mountedRef.current && selectedThreadRef.current === activeUid) setMessages(rows);
      }
      pendingTurnRef.current = null;
      if (mountedRef.current && selectedThreadRef.current === activeUid) {
        const responseStatus = String(response.status || "").toLowerCase();
        const degraded = responseStatus === "degraded" || responseStatus === "blocked"
          || returned.some((message) => String(message.status || "").toLowerCase() === "degraded");
        setTurnProgress({
          threadUid: activeUid,
          stage: degraded ? "degraded" : "persisted",
          accepted: true,
          transport: "staged_sse_v1",
          reason: response.reason,
        });
      }
      setInput((current) => current.trim() === content ? "" : current);
      await refreshThreads();
      if (mountedRef.current && response.provider) setReadiness(response.provider);
    } catch (err) {
      if (mountedRef.current && (!activeUid || selectedThreadRef.current === activeUid)) {
        setError(errorText(err));
        if (activeUid) {
          setTurnProgress({
            threadUid: activeUid,
            stage: "failed",
            accepted: serverAccepted,
            reason: errorText(err),
          });
        }
      }
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setLoading(false);
    }
  }, [allowExternalAi, apiToken, input, refreshThreads, startThread, threadUid]);

  if (booting && !readiness) return <LoadingLine text="读取私有顾问状态…" />;

  const badge = readinessLabel(readiness);
  const readinessReason = readiness?.reason
    ? humanizeLlmReason(readiness.reason, "外部模型尚未通过当前生产闸门，本轮不会调用。")
    : null;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span role="status" className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badge.cls}`}>{badge.text}</span>
        <span className="text-[10.5px] text-muted">服务端按当前组织 + 员工校验 · 外发/写业务/费用动作只生成草稿</span>
        <button
          type="button"
          onClick={() => void createEmptyThread()}
          disabled={loading}
          className="ml-auto inline-flex min-h-9 items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[10.5px] text-accent hover:border-accent hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:text-muted"
          aria-label="新建持久会话"
        >
          <Plus size={12} /> 新会话
        </button>
      </div>

      {readinessReason && readiness?.provider_ready === false ? (
        <div className="mb-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[10px] leading-4 text-warn">
          <div>{readinessReason.message}</div>
          {readinessReason.code ? <div className="mt-1 font-mono text-[9px] opacity-70">诊断码：{readinessReason.code}</div> : null}
        </div>
      ) : null}

      {readiness?.knowledge_bridge_ready === false ? (
        <div className="mb-3 rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[10px] leading-4 text-warn">
          知识检索桥已安全关闭：现有检索还未完成组织级隔离，修复前不会冒险读取跨租户数据或意外触发外部费用。
        </div>
      ) : null}

      {threads.length > 0 ? (
        <label className="mb-3 block text-[10px] text-muted">
          持久会话
          <select
            value={threadUid}
            onChange={(event) => selectThread(event.target.value)}
            className="mt-1 min-h-9 w-full rounded-lg border border-line bg-card px-2.5 py-2 text-[11.5px] text-ink outline-none focus:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            aria-label="选择持久会话"
          >
            {threads.map((item) => (
              <option key={item.thread_uid} value={item.thread_uid}>
                {item.title || "未命名会话"}{item.last_message_at ? ` · ${formatLocal(item.last_message_at)}` : ""}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="mb-3 rounded-lg border border-dashed border-line px-3 py-2 text-[10.5px] text-muted">
          尚无服务端会话。第一次发送时自动创建，不再只保存在这个浏览器。
        </div>
      )}

      <div className="max-h-[360px] space-y-2 overflow-auto pr-1" aria-live="polite" aria-busy={messagesLoading || loading}>
        {messages.length === 0 ? (
          <EmptyLine text="可以咨询 KOL、产品、项目、活动、Dealer 与营销下一步；证据不足时必须明确缺口。" />
        ) : messages.slice(-20).map((message) => (
          <div
            key={message.message_uid}
            className={`rounded-[11px] border px-3 py-2 ${message.role === "user" ? "border-accent bg-accent-soft" : message.status === "degraded" ? "border-warn bg-warn-soft" : "border-line bg-card"}`}
          >
            <div className="mb-1 flex items-center gap-2 text-[9.5px] text-muted">
              <span className="font-semibold text-ink-2">{message.role === "user" ? "你" : "营销顾问"}</span>
              <span>{messageStatusLabel(message)}</span>
              {message.created_at ? <span className="ml-auto">{formatLocal(message.created_at)}</span> : null}
            </div>
            <div className="whitespace-pre-wrap text-[11.5px] leading-5 text-ink">{message.content_text || "—"}</div>
          </div>
        ))}
      </div>

      {turnProgress && turnProgress.threadUid === threadUid ? (
        <AdvisorTurnProgress progress={turnProgress} />
      ) : null}

      <form
        className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          rows={2}
          placeholder="例如：结合现有证据，给我 3 个优先联系的海外 KOL，并说明缺失数据"
          className="min-w-0 flex-1 resize-y rounded-xl border border-line bg-card px-3 py-2 text-[11.5px] text-ink outline-none placeholder:text-muted focus:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          aria-label="向营销顾问提问"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="inline-flex min-h-[38px] flex-none items-center justify-center gap-1.5 rounded-xl border border-accent bg-accent-soft px-3 text-[11.5px] font-semibold text-accent hover:border-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
        >
          <SendHorizontal size={13} /> {loading ? "处理中" : "发送"}
        </button>
      </form>
      <label className="mt-2 flex min-h-8 items-center gap-2 text-[10px] text-muted">
        <input
          type="checkbox"
          checked={allowExternalAi}
          onChange={(event) => setAllowExternalAi(event.target.checked)}
          disabled={!readiness?.provider_ready || loading}
          className="h-3.5 w-3.5 rounded border-line accent-[var(--accent)]"
        />
        {readiness?.provider_ready
          ? "仅本次允许把当前员工的安全上下文发送给已验证模型（仍受独立预算与幂等闸控制）"
          : "外部模型尚未通过精确模型与预算闸；当前仅使用私有本地上下文"}
      </label>
      {error ? <div role="alert" className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[10.5px] text-crit">{error}</div> : null}
    </div>
  );
}

export function MarketingAdvisorBody({ apiToken }: { apiToken: string }) {
  if (!apiToken) return <PendingCard>登录后可使用私有营销顾问；会话由服务端按组织与当前员工校验。</PendingCard>;
  return <MarketingAdvisorSession key={apiToken} apiToken={apiToken} />;
}

function AdvisorMemorySession({ apiToken }: { apiToken: string }) {
  const [snapshot, setSnapshot] = React.useState<AdvisorMemorySnapshot | null>(null);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const busyRef = React.useRef(false);
  const mountedRef = React.useRef(true);

  React.useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const reload = React.useCallback(async () => {
    const next = await getAdvisorMemory(apiToken);
    if (mountedRef.current) setSnapshot(next);
  }, [apiToken]);

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    getAdvisorMemory(apiToken)
      .then((next) => {
        if (alive) setSnapshot(next);
      })
      .catch((err) => {
        if (alive) setError(errorText(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  const run = async (work: () => Promise<unknown>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setLoading(true);
    setError("");
    try {
      await work();
      await reload();
    } catch (err) {
      if (mountedRef.current) setError(errorText(err));
    } finally {
      busyRef.current = false;
      if (mountedRef.current) setLoading(false);
    }
  };

  if (!snapshot && loading) return <LoadingLine text="读取个人记忆…" />;
  if (!snapshot) return <PendingCard>个人记忆尚未就绪。{error ? ` ${error}` : ""}</PendingCard>;

  const paused = snapshot.settings.state === "paused";
  const pending = snapshot.candidates.filter((item) => item.status === "pending");
  const facts = snapshot.facts.filter((item) => item.status !== "deleted");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span role="status" className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${paused ? "border-warn bg-warn-soft text-warn" : "border-good bg-good-soft text-good"}`}>
          {paused ? "记忆已暂停" : "记忆已开启"}
        </span>
        <span className="text-[10px] text-muted">{facts.filter((item) => item.status === "active").length} 条生效 · {pending.length} 条待确认 · 读取窗口 {snapshot.settings.retention_days} 天</span>
        <button
          type="button"
          onClick={() => void run(() => updateAdvisorMemorySettings(apiToken, paused ? "active" : "paused", snapshot.settings.retention_days))}
          disabled={loading}
          className="ml-auto inline-flex min-h-9 items-center gap-1 rounded-lg border border-line px-2.5 py-1 text-[10.5px] text-ink-2 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:text-muted"
        >
          {paused ? <Play size={11} /> : <Pause size={11} />} {paused ? "恢复" : "暂停"}
        </button>
      </div>

      <div className="rounded-lg border border-line bg-panel px-3 py-2 text-[10px] leading-4 text-muted">
        系统不会自动把聊天当作事实。任何学习内容先成为候选，只有你明确确认后才会进入当前员工的私有记忆。超出读取窗口的记忆不会返回，未经授权不会物理删除历史行。
      </div>

      <form
        className="mt-3 flex flex-col gap-2 sm:flex-row"
        onSubmit={(event) => {
          event.preventDefault();
          const summary = input.trim();
          if (!summary) return;
          void run(async () => {
            await createAdvisorMemoryCandidate(apiToken, summary);
            if (mountedRef.current) setInput("");
          });
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={paused || loading}
          placeholder={paused ? "恢复记忆后可新增候选" : "例如：优先推荐美国本土、非中文区的摄影创作者"}
          className="min-h-9 min-w-0 flex-1 rounded-lg border border-line bg-card px-2.5 py-2 text-[11px] text-ink outline-none placeholder:text-muted focus:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:text-muted"
          aria-label="新增个人记忆候选"
        />
        <button
          type="submit"
          disabled={paused || loading || !input.trim()}
          className="min-h-9 flex-none rounded-lg border border-accent bg-accent-soft px-2.5 text-[10.5px] font-semibold text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:border-line disabled:bg-card disabled:text-muted"
        >
          提出候选
        </button>
      </form>

      {pending.length > 0 ? (
        <div className="mt-3">
          <div className="mb-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-muted">待你确认</div>
          <div className="space-y-1.5">
            {pending.slice(0, 6).map((candidate) => (
              <div key={candidate.candidate_uid} className="flex items-start gap-2 rounded-lg border border-warn bg-warn-soft px-2.5 py-2">
                <span className="min-w-0 flex-1 text-[10.5px] leading-4 text-ink">{candidate.summary || candidate.memory_key}</span>
                <button
                  type="button"
                  onClick={() => void run(() => confirmAdvisorMemoryCandidate(apiToken, candidate.candidate_uid))}
                  disabled={paused || loading}
                  className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-md border border-good p-1 text-good hover:bg-good-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-good disabled:text-muted"
                  aria-label={`确认记忆：${candidate.summary}`}
                ><Check size={11} /></button>
                <button
                  type="button"
                  onClick={() => void run(() => rejectAdvisorMemoryCandidate(apiToken, candidate.candidate_uid))}
                  disabled={loading}
                  className="inline-flex min-h-8 min-w-8 items-center justify-center rounded-md border border-line p-1 text-muted hover:border-crit hover:text-crit focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-crit disabled:text-muted"
                  aria-label={`拒绝记忆：${candidate.summary}`}
                ><X size={11} /></button>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-3">
        <div className="mb-1 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-muted">已确认记忆</div>
        {facts.length === 0 ? <EmptyLine text="暂无已确认记忆。" /> : (
          <div className="space-y-1.5">
            {facts.slice(0, 8).map((fact) => (
              <div key={fact.fact_uid} className="flex items-start gap-2 rounded-lg border border-line bg-card px-2.5 py-2">
                <div className="min-w-0 flex-1">
                  <div className="text-[10.5px] leading-4 text-ink">{fact.summary || fact.memory_key}</div>
                  <div className="mt-0.5 text-[9px] text-muted">{fact.status === "active" ? "生效中" : "已暂停"}{fact.version ? ` · v${fact.version}` : ""}</div>
                </div>
                <button
                  type="button"
                  onClick={() => void run(() => updateAdvisorMemoryFact(apiToken, fact.fact_uid, fact.status === "active" ? "paused" : "active"))}
                  disabled={loading}
                  className="min-h-8 rounded-md border border-line px-2 py-1 text-[10px] text-muted hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  {fact.status === "active" ? "暂停" : "恢复"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
      {error ? <div role="alert" className="mt-2 rounded-lg border border-crit bg-crit-soft px-3 py-1.5 text-[10.5px] text-crit">{error}</div> : null}
    </div>
  );
}

export function AdvisorMemoryBody({ apiToken }: { apiToken: string }) {
  if (!apiToken) return <PendingCard>登录后才能读取当前员工的私有记忆。</PendingCard>;
  return <AdvisorMemorySession key={apiToken} apiToken={apiToken} />;
}
