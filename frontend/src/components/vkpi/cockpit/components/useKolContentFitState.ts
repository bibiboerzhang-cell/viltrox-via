import React from "react";

import {
  analyzeKolPoolContentFit,
  getKolPoolContentFit,
} from "../../../../services/vkpi/kolPool-api";

const ACTIVE_STATES = new Set(["queued", "running", "retrying", "processing", "pending"]);
const TERMINAL_STATES = new Set([
  "blocked",
  "failed",
  "error",
  "cancelled",
  "canceled",
  "ai_disabled",
  "insufficient_evidence",
  "unavailable",
]);

export const CONTENT_FIT_POLL_TIMEOUT_MS = 15 * 60_000;

type Row = Record<string, any>;

function record(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function text(value: unknown, limit = 180): string {
  return String(value ?? "").trim().slice(0, limit);
}

export function contentFitPollDelayMs(elapsedMs: number): number {
  if (elapsedMs < 30_000) return 2_000;
  if (elapsedMs < 2 * 60_000) return 5_000;
  return 10_000;
}

export function contentFitSnapshot(payload: unknown) {
  const body = record(payload);
  const job = record(body.analysis_job);
  const state = text(job.state ?? body.state ?? job.status ?? body.status).toLowerCase() || "missing";
  const status = text(body.status ?? job.status ?? state).toLowerCase();
  const jobId = Number(body.job_id ?? job.id) || 0;
  return {
    body,
    job,
    state,
    status,
    jobId,
    ready: state === "ready",
    stale: state === "stale",
    active: ACTIVE_STATES.has(state) && (state !== "pending" || jobId > 0),
    terminal: TERMINAL_STATES.has(state) || TERMINAL_STATES.has(status),
    stage: text(job.stage, 100),
    reason: text(job.reason_detail ?? job.reason ?? job.error_category ?? body.reason, 240),
  };
}

function activeMessage(snapshot: ReturnType<typeof contentFitSnapshot>): string {
  const lead = snapshot.state === "queued"
    ? "内容契合深析已排队"
    : snapshot.state === "retrying"
      ? "内容契合深析等待重试"
      : "内容契合深析处理中";
  return [lead, snapshot.stage && `阶段:${snapshot.stage}`, snapshot.reason]
    .filter(Boolean)
    .join(" · ");
}

function terminalMessage(snapshot: ReturnType<typeof contentFitSnapshot>): string {
  if (snapshot.status === "insufficient_evidence" || snapshot.state === "insufficient_evidence") {
    return "该 KOL 暂无可用视频分析证据，无法做内容契合深析（不杜撰）。";
  }
  if (snapshot.status === "ai_disabled" || snapshot.state === "ai_disabled") {
    return "内容契合 AI 当前未启用，任务没有调用模型。";
  }
  return ["内容契合深析未完成", snapshot.stage && `阶段:${snapshot.stage}`, snapshot.reason || snapshot.state]
    .filter(Boolean)
    .join(" · ");
}

export function useKolContentFitState({
  apiToken,
  kolPoolId,
  productSku = "",
  canAnalyze,
}: {
  apiToken: string;
  kolPoolId: string | number | null | undefined;
  productSku?: string;
  canAnalyze: boolean;
}) {
  const normalizedId = String(kolPoolId ?? "").trim();
  const requestId = typeof kolPoolId === "number" ? kolPoolId : normalizedId;
  const normalizedSku = String(productSku || "").trim();
  const identity = `${normalizedId}:${normalizedSku}`;
  const identityRef = React.useRef(identity);
  identityRef.current = identity;
  const pollRef = React.useRef<{
    timer: ReturnType<typeof setTimeout> | null;
    generation: number;
    startedAt: number;
  }>({ timer: null, generation: 0, startedAt: 0 });
  const [contentFit, setContentFit] = React.useState<Row | null>(null);
  const [contentFitBusy, setContentFitBusy] = React.useState(false);
  const [contentFitError, setContentFitError] = React.useState("");

  const clearPoll = React.useCallback(() => {
    if (pollRef.current.timer) clearTimeout(pollRef.current.timer);
    pollRef.current.timer = null;
    pollRef.current.generation += 1;
  }, []);

  const startPoll = React.useCallback((requestedJobId = 0) => {
    if (!apiToken || !normalizedId) return false;
    clearPoll();
    const controller = pollRef.current;
    const generation = controller.generation;
    const expectedIdentity = identity;
    controller.startedAt = Date.now();
    setContentFitBusy(true);

    const isCurrent = () => (
      controller.generation === generation
      && identityRef.current === expectedIdentity
    );
    const schedule = () => {
      const elapsed = Date.now() - controller.startedAt;
      controller.timer = setTimeout(() => void poll(), contentFitPollDelayMs(elapsed));
    };
    const poll = async () => {
      if (!isCurrent()) return;
      const elapsed = Date.now() - controller.startedAt;
      if (elapsed >= CONTENT_FIT_POLL_TIMEOUT_MS) {
        clearPoll();
        setContentFitBusy(false);
        setContentFitError("内容契合深析仍在后台进行；已停止自动轮询，可稍后重开详情继续核验。");
        return;
      }
      try {
        const payload = await getKolPoolContentFit(apiToken, requestId, {
          ...(normalizedSku ? { productSku: normalizedSku } : {}),
          ...(requestedJobId > 0 ? { jobId: requestedJobId } : {}),
        });
        if (!isCurrent()) return;
        const snapshot = contentFitSnapshot(payload);
        if (snapshot.ready) {
          clearPoll();
          setContentFit(snapshot.body);
          setContentFitBusy(false);
          setContentFitError("");
          return;
        }
        if (snapshot.stale) {
          clearPoll();
          setContentFit(snapshot.body);
          setContentFitBusy(false);
          setContentFitError("已有历史内容契合结果，但已过期；可点击重新深析。");
          return;
        }
        if (snapshot.terminal) {
          clearPoll();
          setContentFitBusy(false);
          setContentFitError(terminalMessage(snapshot));
          return;
        }
        if (snapshot.active) setContentFitError(activeMessage(snapshot));
      } catch {
        // A transient read failure is not a job failure; stay within the bounded poll.
      }
      if (isCurrent()) schedule();
    };
    schedule();
    return true;
  }, [apiToken, normalizedId, requestId, normalizedSku, identity, clearPoll]);

  React.useEffect(() => {
    clearPoll();
    setContentFit(null);
    setContentFitBusy(false);
    setContentFitError("");
    if (!apiToken || !normalizedId) return undefined;
    const generation = pollRef.current.generation;
    const expectedIdentity = identity;
    void getKolPoolContentFit(apiToken, requestId, normalizedSku ? { productSku: normalizedSku } : {})
      .then((payload) => {
        if (pollRef.current.generation !== generation || identityRef.current !== expectedIdentity) return;
        const snapshot = contentFitSnapshot(payload);
        if (snapshot.ready) {
          setContentFit(snapshot.body);
        } else if (snapshot.stale) {
          setContentFit(snapshot.body);
          setContentFitError("已有历史内容契合结果，但已过期；可点击重新深析。");
        } else if (snapshot.active) {
          setContentFitError(activeMessage(snapshot));
          startPoll(snapshot.jobId);
        } else if (snapshot.terminal) {
          setContentFitError(terminalMessage(snapshot));
        }
      })
      .catch(() => undefined);
    return clearPoll;
  }, [apiToken, normalizedId, requestId, normalizedSku, identity, clearPoll, startPoll]);

  const handleContentFitAnalyze = React.useCallback((force = false) => {
    if (!apiToken || !normalizedId || contentFitBusy || !canAnalyze) return;
    clearPoll();
    const generation = pollRef.current.generation;
    const expectedIdentity = identity;
    setContentFitBusy(true);
    setContentFitError("");
    void analyzeKolPoolContentFit(apiToken, requestId, {
      force,
      ...(normalizedSku ? { productSku: normalizedSku } : {}),
    })
      .then((payload) => {
        if (pollRef.current.generation !== generation || identityRef.current !== expectedIdentity) return;
        const snapshot = contentFitSnapshot(payload);
        if (snapshot.ready) {
          setContentFit(snapshot.body);
          setContentFitBusy(false);
          return;
        }
        if (snapshot.stale) {
          setContentFit(snapshot.body);
          setContentFitBusy(false);
          setContentFitError("已有历史内容契合结果，但已过期；可点击重新深析。");
          return;
        }
        if (snapshot.active) {
          setContentFitError(activeMessage(snapshot));
          startPoll(snapshot.jobId);
          return;
        }
        setContentFitBusy(false);
        setContentFitError(terminalMessage(snapshot));
      })
      .catch((error: any) => {
        if (pollRef.current.generation !== generation || identityRef.current !== expectedIdentity) return;
        setContentFitBusy(false);
        setContentFitError(
          Number(error?.status || 0) === 403
            ? "请先关注该 KOL；共享条目仅可查看，不能发起付费深析。"
            : "内容契合深析请求失败，请稍后重试。",
        );
      });
  }, [apiToken, normalizedId, requestId, normalizedSku, identity, contentFitBusy, canAnalyze, clearPoll, startPoll]);

  return {
    contentFit,
    contentFitBusy,
    contentFitError,
    handleContentFitAnalyze,
  };
}
