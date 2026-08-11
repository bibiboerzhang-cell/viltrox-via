import React from "react";

import {
  getActionReviewCandidate,
  listActionInbox,
  verifyActionResult,
  type ActionReviewCandidate,
  type ActionReviewCandidateSnapshot,
  type ActionResultVerificationDecision,
} from "../../../../services/vkpi/actionInbox-api";
import { validateActionReviewCandidate } from "../../../../services/vkpi/action-review-candidate";
import { normalizeSha256 } from "../../../../services/vkpi/review-integrity";

type Draft = {
  reason: string;
  evidence: string;
  correlationId: string;
};

type CandidateState = {
  actionId: number;
  status: "loading" | "ready" | "invalid" | "error";
  candidate: ActionReviewCandidate | null;
  snapshot: ActionReviewCandidateSnapshot | null;
  expectedCandidateHash: string;
  expectedDetailHash: string;
  reason: string;
};

function correlationFor(actionId: number): string {
  const suffix = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `action-review-${actionId}-${suffix}`;
}

function pendingReview(row: any): boolean {
  if (String(row?.status || "").trim().toLowerCase() !== "executed") return false;
  const checklist = row?.result_checklist_json;
  const verification = checklist && typeof checklist === "object"
    ? checklist.human_verification
    : null;
  // 后端把任意 human_verification 对象视为已验收；前端必须保持同一口径，
  // 否则会给出一个注定返回 action_result_already_verified 的假操作入口。
  return !(verification && typeof verification === "object");
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "—";
  } catch {
    return "JSON 无法序列化";
  }
}

export function ActionResultReviewQueue({ apiToken }: { apiToken: string }) {
  const [items, setItems] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [note, setNote] = React.useState("");
  const [openId, setOpenId] = React.useState<number | null>(null);
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [candidateState, setCandidateState] = React.useState<CandidateState | null>(null);
  const requestSequence = React.useRef(0);
  const candidateSequence = React.useRef(0);

  const load = React.useCallback(() => {
    if (!apiToken) return;
    const requestId = ++requestSequence.current;
    candidateSequence.current += 1;
    setOpenId(null);
    setDraft(null);
    setCandidateState(null);
    setLoading(true);
    listActionInbox(apiToken, { status: "executed", limit: 200 })
      .then((result) => {
        if (requestId !== requestSequence.current) return;
        if (result?.available === false) {
          setItems([]);
          setError(String(result.reason || "执行结果验收队列不可用"));
          return;
        }
        const rows = Array.isArray(result?.items) ? result.items : [];
        setItems(rows.filter(pendingReview));
        setError("");
      })
      .catch((cause: any) => {
        if (requestId !== requestSequence.current) return;
        setItems([]);
        setError(String(cause?.message || "结果验收队列加载失败"));
      })
      .finally(() => {
        if (requestId === requestSequence.current) setLoading(false);
      });
  }, [apiToken]);

  React.useEffect(() => {
    load();
    return () => {
      requestSequence.current += 1;
      candidateSequence.current += 1;
    };
  }, [load]);

  const toggle = React.useCallback((actionId: number) => {
    if (openId === actionId) {
      candidateSequence.current += 1;
      setOpenId(null);
      setDraft(null);
      setCandidateState(null);
      return;
    }
    setOpenId(actionId);
    setDraft({ reason: "", evidence: "", correlationId: correlationFor(actionId) });
    setCandidateState({
      actionId, status: "loading", candidate: null, snapshot: null,
      expectedCandidateHash: "", expectedDetailHash: "",
      reason: "正在加载候选执行回执",
    });
    setError("");
    setNote("");
    const requestId = ++candidateSequence.current;
    void getActionReviewCandidate(apiToken, actionId)
      .then(async (candidate) => {
        if (requestId !== candidateSequence.current) return;
        const validation = await validateActionReviewCandidate(candidate, actionId);
        if (requestId !== candidateSequence.current) return;
        if (!validation.ok) {
          setCandidateState({
            actionId,
            status: "invalid",
            candidate: null,
            snapshot: null,
            expectedCandidateHash: validation.expectedCandidate,
            expectedDetailHash: validation.expectedDetail,
            reason: validation.reason,
          });
          return;
        }
        setCandidateState({
          actionId,
          status: "ready",
          candidate: validation.candidate,
          snapshot: validation.snapshot,
          expectedCandidateHash: validation.expectedCandidate,
          expectedDetailHash: validation.expectedDetail,
          reason: "候选执行回执与整份候选/详情指纹一致",
        });
      })
      .catch((cause: any) => {
        if (requestId !== candidateSequence.current) return;
        setCandidateState({
          actionId,
          status: "error",
          candidate: null,
          snapshot: null,
          expectedCandidateHash: "",
          expectedDetailHash: "",
          reason: String(cause?.message || "候选执行回执加载失败"),
        });
      });
  }, [apiToken, openId]);

  const submit = React.useCallback(async (
    actionId: number,
    decision: ActionResultVerificationDecision,
  ) => {
    if (!draft || busyId != null) return;
    const ready = candidateState?.actionId === actionId && candidateState.status === "ready"
      ? candidateState
      : null;
    const expectedCandidateHash = normalizeSha256(ready?.expectedCandidateHash);
    const expectedDetailHash = normalizeSha256(ready?.expectedDetailHash);
    if (!ready?.candidate || !ready.snapshot || !expectedCandidateHash || !expectedDetailHash) {
      setError("候选执行回执与整份候选/详情指纹未通过校验，禁止形成盲审样本");
      return;
    }
    const evidence = draft.evidence
      .split("\n")
      .map((reference) => reference.trim())
      .filter(Boolean)
      .map((reference) => ({ source: "manual", type: "reference", reference }));
    if (
      !draft.reason.trim()
      || evidence.length === 0
      || evidence.length > 20
      || evidence.some(({ reference }) => reference.length < 4 || reference.length > 500)
    ) {
      setError("人工复核必须填写判断原因，并提供 1–20 条、每条 4–500 字的人工依据");
      return;
    }
    setBusyId(actionId);
    setError("");
    try {
      const receipt = await verifyActionResult(apiToken, actionId, {
        decision,
        reason: draft.reason.trim(),
        evidence,
        correlation_id: draft.correlationId,
        expected_candidate_sha256: expectedCandidateHash,
        expected_execution_ledger_id: ready.snapshot.execution_ledger_id,
        expected_detail_sha256: expectedDetailHash,
      });
      setItems((rows) => rows.filter((row) => Number(row.id) !== actionId));
      setOpenId(null);
      setDraft(null);
      setCandidateState(null);
      setNote(
        `动作 #${receipt.action_id} 已${decision === "accepted" ? "复核通过" : "复核驳回"}` +
        ` · 审计台账 #${receipt.ledger_id}`,
      );
    } catch (cause: any) {
      setError(String(cause?.message || "结果验收失败"));
    } finally {
      setBusyId(null);
    }
  }, [apiToken, busyId, candidateState, draft]);

  if (!apiToken) return null;
  return (
    <div className="mt-3 rounded-lg border border-white/[0.08] bg-black/15 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[10px] font-semibold text-slate-200">执行结果人工复核</div>
          <div className="text-[9px] text-slate-500">执行成功不等于业务成功；先核对不可变执行回执，再形成带人工依据的复核样本</div>
        </div>
        <button type="button" onClick={load} className="text-[9px] text-sky-300">
          {loading ? "加载中…" : `待复核 ${items.length}`}
        </button>
      </div>
      {error ? <div className="mt-2 text-[9px] text-red-300">{error}</div> : null}
      {note ? <div className="mt-2 text-[9px] text-emerald-300">{note}</div> : null}
      {!loading && items.length === 0 && !error ? (
        <div className="mt-2 text-[9px] text-slate-500">暂无待人工复核的已执行动作</div>
      ) : null}
      <div className="mt-2 space-y-1.5">
        {items.map((item) => {
          const actionId = Number(item.id);
          const open = openId === actionId;
          return (
            <div key={actionId} className="rounded border border-white/[0.06] bg-white/[0.02] p-2">
              <button type="button" onClick={() => toggle(actionId)} className="w-full text-left">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[10px] text-slate-200">#{actionId} · {item.title || item.category}</span>
                  <span className="shrink-0 text-[9px] text-sky-300">{open ? "收起" : "查看候选回执"}</span>
                </div>
                <div className="mt-0.5 truncate text-[9px] text-slate-500">
                  {item.result_checklist_json?.outcome || "tool completed"} · {item.updated_at || ""}
                </div>
              </button>
              {open && draft ? (
                <div className="mt-2 space-y-1.5 border-t border-white/[0.06] pt-2">
                  {candidateState?.actionId === actionId && candidateState.snapshot ? (
                    <div className="space-y-1.5 rounded border border-white/[0.06] bg-black/20 p-2">
                      <div className="grid gap-1 text-[9px] text-slate-500 sm:grid-cols-2">
                        <div>执行台账：<span className="text-slate-300">#{candidateState.snapshot.execution_ledger_id}</span></div>
                        <div>执行时间：<span className="text-slate-300">{candidateState.snapshot.execution_created_at || "未纳入绑定候选"}</span></div>
                        <div>端点：<span className="break-all text-slate-300">{candidateState.snapshot.endpoint}</span></div>
                        <div>结果：<span className="text-slate-300">{candidateState.snapshot.outcome}</span></div>
                        <div>工具运行：<span className="text-slate-300">{
                          candidateState.snapshot.tool_run_ids?.length
                            ? candidateState.snapshot.tool_run_ids.join(", ")
                            : "未纳入绑定候选"
                        }</span></div>
                        <div className="break-all sm:col-span-2">整份候选 SHA-256：<span className="font-mono text-slate-300">{candidateState.expectedCandidateHash}</span></div>
                        <div className="break-all sm:col-span-2">详情 SHA-256：<span className="font-mono text-slate-300">{candidateState.expectedDetailHash}</span></div>
                      </div>
                      <div className="text-[9px] text-slate-500">
                        验证计划：{candidateState.snapshot.verification_plan.length
                          ? candidateState.snapshot.verification_plan.join("；")
                          : "未登记"}
                      </div>
                      <details>
                        <summary className="cursor-pointer text-[9px] text-sky-300">完整执行详情 JSON</summary>
                        <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap text-[9px] text-slate-400">{prettyJson(candidateState.snapshot.detail_json)}</pre>
                      </details>
                    </div>
                  ) : null}
                  <div
                    role="status"
                    className={`text-[9px] ${
                      candidateState?.status === "ready"
                        ? "text-emerald-300"
                        : candidateState?.status === "loading"
                          ? "text-amber-300"
                          : "text-red-300"
                    }`}
                  >
                    {candidateState?.actionId === actionId ? candidateState.reason : "候选执行回执尚未加载"}
                  </div>
                  <input
                    aria-label="复核原因"
                    value={draft.reason}
                    onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
                    placeholder="为什么接受或拒绝这个结果"
                    maxLength={500}
                    className="h-7 w-full rounded border border-white/10 bg-black/20 px-2 text-[10px] text-slate-100"
                  />
                  <textarea
                    aria-label="人工依据"
                    value={draft.evidence}
                    onChange={(event) => setDraft({ ...draft, evidence: event.target.value })}
                    placeholder="每行一条人工依据：URL、receipt:编号、project:编号或 ledger:编号"
                    rows={2}
                    className="w-full rounded border border-white/10 bg-black/20 px-2 py-1 text-[10px] text-slate-100"
                  />
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      disabled={busyId != null || candidateState?.actionId !== actionId || candidateState.status !== "ready"}
                      onClick={() => void submit(actionId, "accepted")}
                      className="rounded bg-emerald-500/80 px-2 py-1 text-[9px] text-white disabled:opacity-40"
                    >
                      复核通过并记录样本
                    </button>
                    <button
                      type="button"
                      disabled={busyId != null || candidateState?.actionId !== actionId || candidateState.status !== "ready"}
                      onClick={() => void submit(actionId, "rejected")}
                      className="rounded border border-red-500/30 px-2 py-1 text-[9px] text-red-300 disabled:opacity-40"
                    >
                      驳回并记录复核样本
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
