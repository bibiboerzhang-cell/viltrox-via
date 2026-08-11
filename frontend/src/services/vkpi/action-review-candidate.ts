import type { ActionReviewCandidate, ActionReviewCandidateSnapshot } from "./actionInbox-api";
import {
  hasReviewDetail,
  normalizeSha256,
  parseReviewJsonSnapshot,
  reviewHashReasonLabel,
  reviewJsonValuesEqual,
  reviewSnapshotSafetyFindings,
  verifyReviewJsonStringHash,
} from "./review-integrity";

type HashState = {
  expectedCandidate: string;
  actualCandidate: string | null;
  expectedDetail: string;
  actualDetail: string | null;
};

export type ActionCandidateValidation = HashState & (
  | {
      ok: true;
      candidate: ActionReviewCandidate;
      snapshot: ActionReviewCandidateSnapshot;
    }
  | { ok: false; reason: string }
);

function failure(reason: string, hashes?: Partial<HashState>): ActionCandidateValidation {
  return {
    ok: false,
    reason,
    expectedCandidate: hashes?.expectedCandidate || "",
    actualCandidate: hashes?.actualCandidate || null,
    expectedDetail: hashes?.expectedDetail || "",
    actualDetail: hashes?.actualDetail || null,
  };
}

export async function validateActionReviewCandidate(
  candidate: ActionReviewCandidate,
  actionId: number,
): Promise<ActionCandidateValidation> {
  const problems: string[] = [];
  if (Number(candidate.action_id) !== actionId) problems.push("action_id 不一致");
  if (typeof candidate.candidate_canonical_json !== "string" || !candidate.candidate_canonical_json.trim()) {
    problems.push("候选 canonical JSON 缺失");
  }
  if (typeof candidate.detail_json_canonical !== "string" || !candidate.detail_json_canonical.trim()) {
    problems.push("详情 canonical JSON 缺失");
  }
  if (!normalizeSha256(candidate.candidate_sha256)) problems.push("候选 SHA-256 无效");
  if (!normalizeSha256(candidate.detail_sha256)) problems.push("详情 SHA-256 无效");
  if (problems.length > 0) return failure(`候选回执不可复核：${problems.join("；")}`);

  const [candidateCheck, detailCheck] = await Promise.all([
    verifyReviewJsonStringHash(candidate.candidate_canonical_json, candidate.candidate_sha256),
    verifyReviewJsonStringHash(candidate.detail_json_canonical, candidate.detail_sha256),
  ]);
  const hashes: HashState = {
    expectedCandidate: candidateCheck.expected,
    actualCandidate: candidateCheck.actual,
    expectedDetail: detailCheck.expected,
    actualDetail: detailCheck.actual,
  };
  if (!candidateCheck.valid) return failure(`候选快照：${reviewHashReasonLabel(candidateCheck.reason)}`, hashes);
  if (!detailCheck.valid) return failure(`详情快照：${reviewHashReasonLabel(detailCheck.reason)}`, hashes);

  const parsedCandidate = parseReviewJsonSnapshot(candidate.candidate_canonical_json);
  const parsedDetail = parseReviewJsonSnapshot(candidate.detail_json_canonical);
  if (!parsedCandidate.ok) return failure(parsedCandidate.reason, hashes);
  if (!parsedDetail.ok) return failure(parsedDetail.reason, hashes);
  if (!parsedCandidate.value || typeof parsedCandidate.value !== "object" || Array.isArray(parsedCandidate.value)) {
    return failure("canonical JSON 候选形状无效", hashes);
  }
  const record = parsedCandidate.value as Record<string, unknown>;
  const detail = record.detail_json ?? record.detail;
  const ledgerId = Number(record.execution_ledger_id);
  const plan = record.verification_plan;
  const snapshotProblems: string[] = [];
  if (!Number.isInteger(ledgerId) || ledgerId <= 0) snapshotProblems.push("执行台账编号缺失");
  if (!String(record.endpoint || "").trim()) snapshotProblems.push("执行端点缺失");
  if (String(record.outcome || "").trim().toLowerCase() !== "success") snapshotProblems.push("不是成功执行回执");
  if (!hasReviewDetail(detail)) snapshotProblems.push("执行详情缺失");
  if (!Array.isArray(plan)) snapshotProblems.push("验证计划缺失");
  if (record.action_id != null && Number(record.action_id) !== actionId) snapshotProblems.push("候选 action_id 不一致");
  if (snapshotProblems.length > 0) return failure(`候选回执不可复核：${snapshotProblems.join("；")}`, hashes);
  if (!reviewJsonValuesEqual(detail, parsedDetail.value)) return failure("候选详情与独立详情快照不一致", hashes);
  if (record.detail_sha256 != null && normalizeSha256(record.detail_sha256) !== detailCheck.expected) {
    return failure("候选内详情 SHA-256 与独立详情快照不一致", hashes);
  }
  if (reviewSnapshotSafetyFindings(record).length > 0) {
    return failure("候选回执包含不可展示字段，已阻断", hashes);
  }

  return {
    ok: true,
    candidate,
    snapshot: {
      action_id: record.action_id == null ? undefined : Number(record.action_id),
      execution_ledger_id: ledgerId,
      execution_created_at: typeof record.execution_created_at === "string" ? record.execution_created_at : undefined,
      endpoint: String(record.endpoint),
      outcome: String(record.outcome),
      detail_json: detail,
      detail_sha256: detailCheck.expected,
      tool_run_ids: Array.isArray(record.tool_run_ids)
        ? record.tool_run_ids.map(Number).filter((id) => Number.isInteger(id) && id > 0)
        : undefined,
      verification_plan: (plan as unknown[]).map(String),
    },
    ...hashes,
  };
}
