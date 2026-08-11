import React from "react";

import { usePermissions } from "../../../../hooks/usePermissions";
import { listActionInbox, type ActionInboxItem } from "../../../../services/vkpi/actionInbox-api";
import {
  validateOutreachBindingStatus,
  validateOutreachReplyCandidate,
  validateStoredOutreachReply,
  type OutreachCandidateValidation,
  type OutreachReplyReviewSnapshot,
} from "../../../../services/vkpi/outreach-reply-candidate";
import {
  createOutreachBinding,
  getOutreachBindingStatus,
  getOutreachReplyReviewCandidate,
  listPendingGtmVerdicts,
  outreachApiError,
  verifyOutreachReply,
  type OutreachBoundStatusResponse,
  type OutreachUnboundStatusResponse,
  type OutreachReplyOutcome,
  type PendingGtmVerdictItem,
} from "../../../../services/vkpi/outreach-truth-api";

type OutreachActionRef = {
  id: number;
  title: string;
  status: string;
  reviewAt: string;
  due: boolean;
};

type RowStatus =
  | { kind: "loading" }
  | { kind: "unbound"; value: OutreachUnboundStatusResponse }
  | { kind: "bound"; value: OutreachBoundStatusResponse }
  | { kind: "verified"; value: OutreachBoundStatusResponse; snapshot: OutreachReplyReviewSnapshot }
  | { kind: "error"; reason: string };

type CandidateState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "invalid" | "error" | "stale"; reason: string }
  | {
      kind: "ready";
      validation: Extract<OutreachCandidateValidation, { ok: true }>;
      expiresAt: number;
      correlationId: string;
    };

type StatusRefreshResult = "unbound" | "bound" | "verified" | "error";

const ACTION_STATUSES = ["approved", "executing", "executed"] as const;
const BINDABLE_STATUSES = new Set<string>(ACTION_STATUSES);

function dict(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function isOutreachBet(payload: unknown): boolean {
  return String(dict(dict(payload).bet).action_type || "").trim().toLowerCase() === "kol_outreach";
}

function correlationFor(kind: "bind" | "reply", id: number, outcome?: OutreachReplyOutcome): string {
  const suffix = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `outreach-${kind}-${id}${outcome ? `-${outcome}` : ""}-${suffix}`.slice(0, 160);
}

function safeReason(error: unknown, fallback: string): string {
  const { status, reason } = outreachApiError(error);
  const clean = String(reason || "").trim();
  if (/^[a-z0-9_ -]{1,120}$/i.test(clean)) return clean;
  return status ? `${fallback}（HTTP ${status}）` : fallback;
}

function eligibilityLabel(reason: string): string {
  const labels: Record<string, string> = {
    eligible: "服务端判定可核验",
    observation_window_open: "观察窗口尚未关闭，请等待后重新取候选",
    verified_inbound_not_observed: "未观察到可核验的入站回复",
    reply_exists: "窗口内已有回复，不能签 no_reply；请改审 replied",
    inbound_content_unreviewable: "入站正文不可人工审阅，需要补证据",
    outbound_content_unreviewable: "首条外联正文不可人工审阅，需要补证据",
    binding_first_outbound_changed: "首条外联已漂移，禁止核验",
  };
  return labels[reason] || `服务端不可核验：${reason || "unknown"}`;
}

function actionFromInbox(item: ActionInboxItem): OutreachActionRef | null {
  const id = Number(item.id);
  if (!Number.isInteger(id) || id <= 0 || item.category !== "gtm_bet" || !isOutreachBet(item.payload_json)) {
    return null;
  }
  const payload = dict(item.payload_json);
  const bet = dict(payload.bet);
  return {
    id,
    title: String(item.title || `GTM 外联 Action #${id}`),
    status: String(item.status || "unknown"),
    reviewAt: String(bet.review_at || payload.review_at || ""),
    due: false,
  };
}

function actionFromPending(item: PendingGtmVerdictItem): OutreachActionRef | null {
  const id = Number(item.bet_inbox_id || item.id);
  if (!Number.isInteger(id) || id <= 0 || !isOutreachBet({ bet: item.bet })) return null;
  return {
    id,
    title: String(item.title || `GTM 外联 Action #${id}`),
    status: String(item.status || "unknown"),
    reviewAt: String(item.review_at || ""),
    due: true,
  };
}

function mergeActions(inboxRows: ActionInboxItem[], pendingRows: PendingGtmVerdictItem[]): OutreachActionRef[] {
  const merged = new Map<number, OutreachActionRef>();
  inboxRows.forEach((item) => {
    const row = actionFromInbox(item);
    if (row) merged.set(row.id, row);
  });
  pendingRows.forEach((item) => {
    const row = actionFromPending(item);
    if (!row) return;
    const previous = merged.get(row.id);
    merged.set(row.id, previous ? { ...previous, due: true, reviewAt: previous.reviewAt || row.reviewAt } : row);
  });
  return Array.from(merged.values()).sort((left, right) => Number(right.due) - Number(left.due) || right.id - left.id);
}

function ReviewContent({ message, label }: {
  message: OutreachReplyReviewSnapshot["first_outbound"];
  label: string;
}) {
  const content = message.review_content;
  return (
    <div className="rounded border border-white/[0.06] bg-black/20 p-2">
      <div className="flex flex-wrap items-center gap-x-2 text-[9px] text-slate-500">
        <span>{label}消息 #{message.message_id}</span>
        <span>{message.created_at}</span>
        <span>host: {content.evidence_host || "无"}</span>
      </div>
      <div className="mt-1 whitespace-pre-wrap break-words text-[10px] text-slate-200">
        {content.body_excerpt || content.snippet_excerpt || "（脱敏后无可展示正文）"}
      </div>
      <div className="mt-1 break-all font-mono text-[8px] text-slate-600">
        evidence ref {content.evidence_ref_sha256}
      </div>
    </div>
  );
}

function CandidateReview({ snapshot, expectedHash }: {
  snapshot: OutreachReplyReviewSnapshot;
  expectedHash: string;
}) {
  return (
    <div className="space-y-1.5 rounded border border-white/[0.07] bg-white/[0.02] p-2">
      <div className="grid gap-1 text-[9px] text-slate-500 sm:grid-cols-2">
        <div>项目 / KOL：<span className="text-slate-300">#{snapshot.project_id} / #{snapshot.kol_id}</span></div>
        <div>Pool / Action：<span className="text-slate-300">#{snapshot.kol_pool_id} / #{snapshot.action_inbox_id}</span></div>
        <div>预测：<span className="break-all text-slate-300">{snapshot.prediction_run_id}</span></div>
        <div>SKU / 渠道：<span className="text-slate-300">{snapshot.product_sku} / {snapshot.channel}</span></div>
        <div className="sm:col-span-2">观察窗：<span className="text-slate-300">{snapshot.observation_start_at} → {snapshot.observation_end_at}</span></div>
        <div>窗口已关闭：<span className={snapshot.window_closed ? "text-emerald-300" : "text-amber-300"}>{snapshot.window_closed ? "是" : "否"}</span></div>
        <div>首条外联仍严格一致：<span className={snapshot.binding_first_outbound_still_exact ? "text-emerald-300" : "text-red-300"}>{snapshot.binding_first_outbound_still_exact ? "是" : "否"}</span></div>
        <div>外联范围无无效候选：<span className={snapshot.outbound_scope_has_no_invalid_candidates ? "text-emerald-300" : "text-red-300"}>{snapshot.outbound_scope_has_no_invalid_candidates ? "是" : "否"}</span></div>
        <div>服务端资格：<span className={snapshot.eligible ? "text-emerald-300" : "text-amber-300"}>{eligibilityLabel(snapshot.eligibility_reason)}</span></div>
        <div className="break-all sm:col-span-2">候选 SHA-256：<span className="font-mono text-slate-300">{expectedHash}</span></div>
      </div>
      <ReviewContent message={snapshot.first_outbound} label="首条外联" />
      <div className="text-[9px] text-amber-300">
        当前仅是经理对可写消息快照的签署；尚无 Provider 同步完整性水位与晚到消息对账，
        因此只作描述性证据，不进入 validated 学习指标。
      </div>
      {snapshot.resolved_inbound ? (
        <>
          <ReviewContent message={snapshot.resolved_inbound} label="首条入站回复" />
          <div className="text-[9px] text-amber-300">入站消息源可由客户端写入；本次结论仅由经理对服务端锁定快照签署。</div>
        </>
      ) : (
        <div className="text-[9px] text-slate-500">服务端在冻结观察窗内未解析到入站回复候选。</div>
      )}
    </div>
  );
}

function OutreachTruthRow({ apiToken, action }: { apiToken: string; action: OutreachActionRef }) {
  const [status, setStatus] = React.useState<RowStatus>({ kind: "loading" });
  const [outcome, setOutcome] = React.useState<OutreachReplyOutcome>("replied");
  const [candidate, setCandidate] = React.useState<CandidateState>({ kind: "idle" });
  const [busy, setBusy] = React.useState<"" | "bind" | "candidate" | "verify">("");
  const [note, setNote] = React.useState("");
  const [error, setError] = React.useState("");
  const bindCorrelation = React.useRef(correlationFor("bind", action.id));
  const statusSequence = React.useRef(0);
  const candidateSequence = React.useRef(0);

  const discardCandidate = React.useCallback((reason?: string) => {
    candidateSequence.current += 1;
    setCandidate(reason ? { kind: "stale", reason } : { kind: "idle" });
    setBusy((current) => current === "candidate" ? "" : current);
  }, []);

  const refreshStatus = React.useCallback(async (): Promise<StatusRefreshResult> => {
    const requestId = ++statusSequence.current;
    discardCandidate();
    setStatus({ kind: "loading" });
    setError("");
    try {
      const response = await getOutreachBindingStatus(apiToken, action.id);
      if (requestId !== statusSequence.current) return "error";
      const checked = validateOutreachBindingStatus(response, action.id);
      if (!checked.ok) {
        setStatus({ kind: "error", reason: checked.reason });
        return "error";
      }
      if (checked.value.status === "unbound") {
        setStatus({ kind: "unbound", value: checked.value });
        return "unbound";
      }
      if (checked.value.status === "reply_verified") {
        const receipt = checked.value.reply_verification;
        if (!receipt) {
          setStatus({ kind: "error", reason: "已核验状态缺少不可变回执" });
          return "error";
        }
        const stored = await validateStoredOutreachReply(receipt, {
          actionId: action.id,
          binding: checked.value.binding,
        });
        if (requestId !== statusSequence.current) return "error";
        if (!stored.ok) {
          setStatus({ kind: "error", reason: `已存回执完整性校验失败：${stored.reason}` });
          return "error";
        }
        setStatus({ kind: "verified", value: checked.value, snapshot: stored.snapshot });
        return "verified";
      }
      setStatus({ kind: "bound", value: checked.value });
      return "bound";
    } catch (cause) {
      if (requestId !== statusSequence.current) return "error";
      setStatus({ kind: "error", reason: safeReason(cause, "外联真值状态读取失败") });
      return "error";
    }
  }, [action.id, apiToken, discardCandidate]);

  React.useEffect(() => {
    void refreshStatus();
    return () => {
      statusSequence.current += 1;
      candidateSequence.current += 1;
    };
  }, [refreshStatus]);

  React.useEffect(() => {
    if (candidate.kind !== "ready") return undefined;
    const remaining = Math.max(0, candidate.expiresAt - Date.now());
    const timer = globalThis.setTimeout(() => {
      setCandidate({ kind: "stale", reason: "候选已超过服务端 TTL，请重新获取并人工复核" });
    }, remaining);
    return () => globalThis.clearTimeout(timer);
  }, [candidate]);

  const bind = async () => {
    if (
      busy
      || status.kind !== "unbound"
      || !status.value.bindable
      || !BINDABLE_STATUSES.has(action.status)
    ) return;
    setBusy("bind");
    setError("");
    setNote("");
    try {
      const receipt = await createOutreachBinding(apiToken, action.id, bindCorrelation.current);
      const recovered = await refreshStatus();
      if (recovered === "bound" || recovered === "verified") {
        setNote(`绑定回执 #${receipt.id}${receipt.idempotent ? "（幂等复用）" : ""} 已由状态端点复核`);
      } else {
        setError(`写入返回绑定回执 #${receipt.id}，但状态仍未绑定；不得视为已完成，请刷新重查`);
      }
    } catch (cause) {
      const { status: httpStatus, reason } = outreachApiError(cause);
      if (httpStatus === 409 && reason === "outreach_action_already_bound") {
        const recovered = await refreshStatus();
        if (recovered === "bound" || recovered === "verified") {
          setNote("检测到既有绑定，已从状态端点恢复");
        } else {
          setError("服务端报告既有绑定，但状态端点未返回绑定证据；不得视为已恢复");
        }
      } else {
        setError(safeReason(cause, "外联真值绑定失败；可用同一请求重试"));
      }
    } finally {
      setBusy("");
    }
  };

  const chooseOutcome = (next: OutreachReplyOutcome) => {
    if (next === outcome) return;
    setOutcome(next);
    discardCandidate("结论已改变，旧候选已作废；请重新获取并人工审阅");
    setError("");
    setNote("");
  };

  const loadCandidate = async () => {
    if (busy || status.kind !== "bound") return;
    const requestId = ++candidateSequence.current;
    const requestedAt = Date.now();
    setBusy("candidate");
    setCandidate({ kind: "loading" });
    setError("");
    setNote("");
    try {
      const response = await getOutreachReplyReviewCandidate(apiToken, status.value.binding.id, outcome);
      const validation = await validateOutreachReplyCandidate(response, {
        actionId: action.id,
        binding: status.value.binding,
        outcome,
      });
      if (requestId !== candidateSequence.current) return;
      if (!validation.ok) {
        setCandidate({ kind: "invalid", reason: validation.reason });
        return;
      }
      setCandidate({
        kind: "ready",
        validation,
        expiresAt: requestedAt + validation.ttlSeconds * 1000,
        correlationId: correlationFor("reply", status.value.binding.id, outcome),
      });
    } catch (cause) {
      if (requestId === candidateSequence.current) {
        setCandidate({ kind: "error", reason: safeReason(cause, "回复候选读取失败") });
      }
    } finally {
      if (requestId === candidateSequence.current) setBusy("");
    }
  };

  const verify = async () => {
    if (busy || status.kind !== "bound" || candidate.kind !== "ready" || !candidate.validation.canVerify) return;
    if (Date.now() >= candidate.expiresAt) {
      discardCandidate("候选已超过服务端 TTL，请重新获取并人工复核");
      return;
    }
    setBusy("verify");
    setError("");
    setNote("");
    try {
      const receipt = await verifyOutreachReply(apiToken, status.value.binding.id, {
        outcome,
        correlation_id: candidate.correlationId,
        expected_candidate_sha256: candidate.validation.expectedHash,
        candidate_observed_at: candidate.validation.candidateObservedAt,
      });
      const recovered = await refreshStatus();
      if (recovered === "verified") {
        setNote(`回复核验回执 #${receipt.id}${receipt.idempotent ? "（幂等复用）" : ""} 已回读确认`);
      } else {
        setError(`写入返回回执 #${receipt.id}，但状态回读未通过；不得视为已核验，请刷新重查`);
      }
    } catch (cause) {
      const { status: httpStatus, reason } = outreachApiError(cause);
      if (httpStatus === 409) {
        discardCandidate(`服务端拒绝旧候选（${reason}）；必须重新获取并人工复核`);
        if (reason === "outreach_reply_already_verified") {
          const recovered = await refreshStatus();
          if (recovered === "verified") setNote("检测到既有回复核验，已从状态端点恢复");
          else setError("服务端报告已核验，但状态端点未返回核验回执；不得视为已恢复");
        }
      } else {
        setError(safeReason(cause, "回复核验失败；候选与 correlation 已保留，可原样重试"));
      }
    } finally {
      setBusy("");
    }
  };

  const current = candidate.kind === "ready" ? candidate.validation : null;
  const bindable = status.kind === "unbound"
    && status.value.bindable
    && BINDABLE_STATUSES.has(action.status);
  const unboundReason = status.kind === "unbound" && !status.value.bindable
    ? status.value.eligibility_reason
    : `action_status_not_bindable:${action.status}`;
  const receipt = status.kind === "verified" ? status.value.reply_verification : null;

  return (
    <div className="rounded border border-white/[0.07] bg-black/15 p-2.5" data-action-id={action.id}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[10px] font-medium text-slate-200">#{action.id} · {action.title}</div>
          <div className="mt-0.5 text-[9px] text-slate-500">
            Action {action.status}{action.due ? " · 已到复盘日" : ""}{action.reviewAt ? ` · ${action.reviewAt}` : ""}
          </div>
        </div>
        <button type="button" disabled={Boolean(busy)} onClick={() => void refreshStatus()} className="text-[9px] text-sky-300 disabled:opacity-40">
          {status.kind === "loading" ? "读取状态…" : "刷新状态"}
        </button>
      </div>

      {status.kind === "unbound" ? (
        <div className="mt-2">
          <div className="text-[9px] text-amber-300">尚未绑定服务端解析的 Project / 首条外联。</div>
          {!bindable ? (
            <div className="mt-1 text-[9px] text-slate-500">
              当前不可绑定：{unboundReason}
            </div>
          ) : null}
          <button
            type="button"
            disabled={!bindable || Boolean(busy)}
            onClick={() => void bind()}
            className="mt-1.5 rounded border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-[9px] text-sky-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === "bind" ? "绑定中…" : "绑定服务端首条外联"}
          </button>
        </div>
      ) : null}

      {status.kind === "bound" ? (
        <div className="mt-2 space-y-2">
          <div className="grid gap-1 text-[9px] text-slate-500 sm:grid-cols-2">
            <div>绑定 #{status.value.binding.id} · 项目 <span className="text-slate-300">#{status.value.binding.project_id}</span></div>
            <div>预测 <span className="break-all text-slate-300">{status.value.binding.prediction_run_id}</span></div>
            <div>SKU / 渠道 <span className="text-slate-300">{status.value.binding.product_sku} / {status.value.binding.channel}</span></div>
            <div>观察截止 <span className="text-slate-300">{status.value.binding.observation_end_at}</span></div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {(["replied", "no_reply"] as OutreachReplyOutcome[]).map((value) => (
              <button
                key={value}
                type="button"
                disabled={Boolean(busy)}
                onClick={() => chooseOutcome(value)}
                aria-pressed={outcome === value}
                className={`rounded border px-2 py-1 text-[9px] disabled:opacity-40 ${outcome === value ? "border-sky-500/40 bg-sky-500/15 text-sky-200" : "border-white/[0.08] text-slate-400"}`}
              >
                {value === "replied" ? "审阅 replied" : "审阅 no_reply"}
              </button>
            ))}
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => void loadCandidate()}
              className="rounded border border-white/[0.1] px-2 py-1 text-[9px] text-slate-300 disabled:opacity-40"
            >
              {busy === "candidate" ? "获取中…" : "获取服务端候选"}
            </button>
          </div>
          {current ? <CandidateReview snapshot={current.snapshot} expectedHash={current.expectedHash} /> : null}
          {candidate.kind === "loading" ? <div className="text-[9px] text-slate-500">正在校验 canonical JSON、SHA-256 与脱敏字段…</div> : null}
          {["invalid", "error", "stale"].includes(candidate.kind) ? (
            <div className="text-[9px] text-amber-300">{(candidate as Extract<CandidateState, { reason: string }>).reason}</div>
          ) : null}
          <button
            type="button"
            disabled={Boolean(busy) || !current?.canVerify}
            onClick={() => void verify()}
            className="rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[9px] text-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy === "verify" ? "提交并回读…" : `确认并签署 ${outcome}`}
          </button>
        </div>
      ) : null}

      {status.kind === "verified" && receipt ? (
        <div className="mt-2 space-y-1.5">
          <div className="text-[9px] text-emerald-300">
            已核验 {receipt.outcome} · 不可变回执 #{receipt.id} · {receipt.verified_at}
          </div>
          <CandidateReview snapshot={status.snapshot} expectedHash={receipt.review_candidate_sha256} />
        </div>
      ) : null}
      {status.kind === "error" ? <div className="mt-2 text-[9px] text-red-300">{status.reason}</div> : null}
      {error ? <div className="mt-2 text-[9px] text-red-300">{error}</div> : null}
      {note ? <div className="mt-2 text-[9px] text-emerald-300">{note}</div> : null}
    </div>
  );
}

export function OutreachTruthReviewQueue({ apiToken }: { apiToken: string }) {
  const permissions = usePermissions();
  const allowed = permissions.isManager() && permissions.hasPermission("vkpi", "write");
  const [items, setItems] = React.useState<OutreachActionRef[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [warning, setWarning] = React.useState("");
  const requestSequence = React.useRef(0);

  const load = React.useCallback(async () => {
    if (!apiToken || !allowed) return;
    const requestId = ++requestSequence.current;
    setLoading(true);
    setError("");
    setWarning("");
    const warnings: string[] = [];
    const [actionResults, pendingResults] = await Promise.all([
      Promise.allSettled(
        ACTION_STATUSES.map((status) => listActionInbox(apiToken, { category: "gtm_bet", status, limit: 200 })),
      ),
      Promise.allSettled([listPendingGtmVerdicts(apiToken, 200)]),
    ]);
    if (requestId !== requestSequence.current) return;
    const failedAction = actionResults.find((result) => result.status === "rejected");
    if (failedAction?.status === "rejected") {
      setItems([]);
      setError(safeReason(failedAction.reason, "Action 外联队列读取失败"));
      setLoading(false);
      return;
    }
    const inboxResponses = actionResults.map((result) => (
      result.status === "fulfilled" ? result.value : null
    ));
    const unavailable = inboxResponses.find((result) => result?.available === false);
    if (unavailable) {
      setItems([]);
      setError(safeReason({ message: unavailable.reason }, "Action 外联队列不可用"));
      setLoading(false);
      return;
    }
    const inboxRows = inboxResponses.flatMap((result) => Array.isArray(result?.items) ? result.items : []);
    const pendingResult = pendingResults[0];
    let pendingRows: PendingGtmVerdictItem[] = [];
    if (pendingResult.status === "rejected") {
      warnings.push(safeReason(pendingResult.reason, "GTM 到期复盘源读取失败；当前仅显示 Action 队列"));
    } else if (pendingResult.value.status === "error") {
      warnings.push(safeReason(
        { message: pendingResult.value.reason },
        "GTM 到期复盘源不可用；当前仅显示 Action 队列",
      ));
    } else {
      pendingRows = Array.isArray(pendingResult.value.items) ? pendingResult.value.items : [];
      if (pendingResult.value.status === "empty" && pendingResult.value.reason) {
        warnings.push(`GTM 到期复盘源为空：${safeReason(
          { message: pendingResult.value.reason },
          "原因不可展示",
        )}`);
      }
      if (Number(pendingResult.value.due_total || 0) > pendingRows.length) {
        warnings.push(`GTM 到期复盘源仅返回 ${pendingRows.length}/${pendingResult.value.due_total} 条；当前为有界队列`);
      }
    }
    const boundedInbox = inboxResponses.some((result) => (result?.items?.length || 0) >= 200);
    if (boundedInbox) warnings.push("Action Inbox 单状态最多回读 200 条；当前为有界队列");
    setWarning(warnings.join("；"));
    setItems(mergeActions(inboxRows, pendingRows));
    setLoading(false);
  }, [allowed, apiToken]);

  React.useEffect(() => {
    if (allowed) void load();
    return () => {
      requestSequence.current += 1;
    };
  }, [allowed, load]);

  if (!apiToken || !allowed) return null;
  return (
    <div className="mt-3 rounded-lg border border-white/[0.08] bg-black/15 p-2.5" data-testid="outreach-truth-review-queue">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[10px] font-semibold text-slate-200">外联回复真值复核 · 经理</div>
          <div className="text-[9px] text-slate-500">Action → 服务端首条外联 → 脱敏候选 → 人工签署；不发送消息，也不接受客户端消息 ID</div>
        </div>
        <button type="button" onClick={() => void load()} className="shrink-0 text-[9px] text-sky-300">
          {loading ? "加载中…" : `外联 Action ${items.length}`}
        </button>
      </div>
      {error ? <div className="mt-2 text-[9px] text-red-300">{error}</div> : null}
      {warning ? <div className="mt-2 text-[9px] text-amber-300">{warning}</div> : null}
      {!loading && !error && !warning && items.length === 0 ? (
        <div className="mt-2 text-[9px] text-slate-500">当前有界 Action / 到期复盘源中暂无外联动作</div>
      ) : null}
      <div className="mt-2 space-y-2">
        {items.map((item) => <OutreachTruthRow key={item.id} apiToken={apiToken} action={item} />)}
      </div>
    </div>
  );
}
