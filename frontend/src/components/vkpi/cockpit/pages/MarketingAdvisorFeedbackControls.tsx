import React from "react";
import { Check, ThumbsDown, ThumbsUp } from "lucide-react";

import {
  postAdvisorMessageFeedback,
  type AdvisorFeedbackResponse,
  type AdvisorMessage,
} from "../../../../services/vkpi/marketing-advisor-api";

function feedbackRequestId(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {
    // Bounded fallback below.
  }
  return `advisor-feedback-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function MarketingAdvisorFeedbackControls({
  apiToken,
  message,
  onSaved,
}: {
  apiToken: string;
  message: AdvisorMessage;
  onSaved: (response: AdvisorFeedbackResponse) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [correction, setCorrection] = React.useState(message.feedback?.correction_text || "");
  const [proposeMemory, setProposeMemory] = React.useState(Boolean(message.feedback?.propose_memory));
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [candidateSaved, setCandidateSaved] = React.useState(Boolean(message.feedback?.candidate_uid));
  const pendingRequestRef = React.useRef<{
    payloadKey: string;
    requestId: string;
    observedAt: string;
  } | null>(null);

  const submit = async (rating: "helpful" | "unhelpful") => {
    if (loading) return;
    if (rating === "unhelpful" && proposeMemory && !correction.trim()) {
      setError("保存为个人记忆候选前，请先填写纠正内容。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payloadKey = JSON.stringify({
        rating,
        correction: rating === "unhelpful" ? correction.trim() : "",
        proposeMemory: rating === "unhelpful" && proposeMemory,
        contextRefs: message.context_refs_json || [],
      });
      const prior = pendingRequestRef.current;
      const requestId = prior?.payloadKey === payloadKey ? prior.requestId : feedbackRequestId();
      const observedAt = prior?.payloadKey === payloadKey ? prior.observedAt : new Date().toISOString();
      pendingRequestRef.current = { payloadKey, requestId, observedAt };
      const response = await postAdvisorMessageFeedback(
        apiToken,
        message.thread_uid,
        message.message_uid,
        {
          rating,
          correctionText: rating === "unhelpful" ? correction.trim() : "",
          proposeMemory: rating === "unhelpful" && proposeMemory,
          contextRefs: message.context_refs_json || [],
          provenance: {
            source_ref: "explicit:advisor-workspace-feedback",
            observed_at: observedAt,
          },
          clientRequestId: requestId,
        },
      );
      pendingRequestRef.current = null;
      setCandidateSaved(Boolean(response.candidate || response.feedback.candidate_uid));
      setEditing(false);
      onSaved(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "反馈保存失败");
    } finally {
      setLoading(false);
    }
  };

  const current = message.feedback?.rating;
  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => void submit("helpful")}
          disabled={loading}
          className={`inline-flex min-h-7 items-center gap-1 rounded-md border px-2 text-[9.5px] ${current === "helpful" ? "border-good bg-good-soft text-good" : "border-line text-muted hover:border-good hover:text-good"}`}
          aria-label={`标记有用：${message.message_uid}`}
        >
          <ThumbsUp size={11} /> 有用
        </button>
        <button
          type="button"
          onClick={() => setEditing(true)}
          disabled={loading}
          className={`inline-flex min-h-7 items-center gap-1 rounded-md border px-2 text-[9.5px] ${current === "unhelpful" ? "border-warn bg-warn-soft text-warn" : "border-line text-muted hover:border-warn hover:text-warn"}`}
          aria-label={`标记无用或纠正：${message.message_uid}`}
        >
          <ThumbsDown size={11} /> 无用 / 纠正
        </button>
        {current ? <span className="text-[9px] text-muted"><Check size={10} className="mr-0.5 inline" />反馈已留存</span> : null}
        {candidateSaved ? <span className="text-[9px] text-warn">记忆候选待你确认，尚未生效</span> : null}
      </div>

      {editing ? (
        <div className="mt-2 rounded-lg border border-line bg-panel p-2">
          <textarea
            value={correction}
            onChange={(event) => setCorrection(event.target.value)}
            rows={2}
            maxLength={4000}
            placeholder="可选：写下正确口径、偏好或缺失证据"
            className="w-full resize-y rounded-lg border border-line bg-card px-2.5 py-2 text-[10.5px] text-ink outline-none placeholder:text-muted focus:border-accent"
            aria-label={`纠正顾问回复：${message.message_uid}`}
          />
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1.5 text-[9.5px] text-muted">
              <input
                type="checkbox"
                checked={proposeMemory}
                onChange={(event) => setProposeMemory(event.target.checked)}
                className="h-3.5 w-3.5 rounded border-line accent-[var(--accent)]"
              />
              仅保存为个人记忆候选（仍需手动确认）
            </label>
            <button
              type="button"
              onClick={() => void submit("unhelpful")}
              disabled={loading || (proposeMemory && !correction.trim())}
              className="ml-auto min-h-7 rounded-md border border-warn bg-warn-soft px-2 text-[9.5px] font-semibold text-warn disabled:border-line disabled:bg-card disabled:text-muted"
            >
              {loading ? "保存中" : "提交无用反馈"}
            </button>
          </div>
          <div className="mt-1 text-[9px] text-muted">反馈不会自动训练模型、修改权重或激活个人记忆。</div>
        </div>
      ) : null}
      {error ? <div role="alert" className="mt-1 text-[9.5px] text-crit">{error}</div> : null}
    </div>
  );
}
