import type { SkillReviewCandidate, SkillRunRow } from "./skills-api";
import {
  hasReviewDetail,
  normalizeSha256,
  parseReviewJsonSnapshot,
  reviewHashReasonLabel,
  reviewSnapshotSafetyFindings,
  verifyReviewJsonStringHash,
} from "./review-integrity";

type HashState = {
  expectedInput: string;
  actualInput: string | null;
  expectedOutput: string;
  actualOutput: string | null;
};

export type SkillCandidateValidation = HashState & (
  | {
      ok: true;
      candidate: SkillReviewCandidate;
      inputSnapshot: Record<string, unknown>;
      outputSnapshot: unknown;
    }
  | { ok: false; reason: string }
);

function failure(reason: string, hashes?: Partial<HashState>): SkillCandidateValidation {
  return {
    ok: false,
    reason,
    expectedInput: hashes?.expectedInput || "",
    actualInput: hashes?.actualInput || null,
    expectedOutput: hashes?.expectedOutput || "",
    actualOutput: hashes?.actualOutput || null,
  };
}

export async function validateSkillReviewCandidate(
  candidate: SkillReviewCandidate,
  run: Pick<SkillRunRow, "id" | "skill_name" | "output_sha256">,
): Promise<SkillCandidateValidation> {
  const problems: string[] = [];
  if (Number(candidate.run_id) !== run.id) problems.push("run_id 不一致");
  if (String(candidate.skill_name || "").trim() !== run.skill_name) problems.push("skill_name 不一致");
  if (typeof candidate.input_snapshot_json !== "string" || !candidate.input_snapshot_json.trim()) problems.push("运行输入 canonical JSON");
  if (typeof candidate.output_snapshot_json !== "string" || !candidate.output_snapshot_json.trim()) problems.push("运行输出 canonical JSON");
  const inputHash = normalizeSha256(candidate.input_sha256);
  const outputHash = normalizeSha256(candidate.output_sha256);
  if (!inputHash) problems.push("输入 SHA-256");
  if (!outputHash) problems.push("输出 SHA-256");
  const listHash = normalizeSha256(run.output_sha256);
  if (listHash && outputHash && listHash !== outputHash) problems.push("列表与候选输出 SHA-256 不一致");
  if (!String(candidate.model_used || "").trim()) problems.push("模型");
  if (!String(candidate.prompt_version || "").trim()) problems.push("提示版本");
  if (candidate.redacted === false) problems.push("服务端脱敏标识无效");
  if (problems.length > 0) return failure(`复核候选不可用：${problems.join("、")}`);

  const [inputCheck, outputCheck] = await Promise.all([
    verifyReviewJsonStringHash(candidate.input_snapshot_json, candidate.input_sha256),
    verifyReviewJsonStringHash(candidate.output_snapshot_json, candidate.output_sha256),
  ]);
  const hashes: HashState = {
    expectedInput: inputCheck.expected,
    actualInput: inputCheck.actual,
    expectedOutput: outputCheck.expected,
    actualOutput: outputCheck.actual,
  };
  if (!inputCheck.valid) return failure(`输入快照：${reviewHashReasonLabel(inputCheck.reason)}`, hashes);
  if (!outputCheck.valid) return failure(`输出快照：${reviewHashReasonLabel(outputCheck.reason)}`, hashes);

  const parsedInput = parseReviewJsonSnapshot(candidate.input_snapshot_json);
  const parsedOutput = parseReviewJsonSnapshot(candidate.output_snapshot_json);
  if (!parsedInput.ok) return failure(parsedInput.reason, hashes);
  if (!parsedOutput.ok) return failure(parsedOutput.reason, hashes);
  if (!parsedInput.value || typeof parsedInput.value !== "object" || Array.isArray(parsedInput.value)) {
    return failure("canonical JSON 运行输入形状无效", hashes);
  }
  if (!hasReviewDetail(parsedOutput.value)) return failure("canonical JSON 运行输出详情缺失", hashes);
  const inputSnapshot = parsedInput.value as Record<string, unknown>;
  const outputSnapshot = parsedOutput.value;
  if (reviewSnapshotSafetyFindings({
    input_snapshot: inputSnapshot,
    output_snapshot: outputSnapshot,
    model_used: candidate.model_used,
    prompt_version: candidate.prompt_version,
  }).length > 0) {
    return failure("复核候选包含不可展示字段，已阻断", hashes);
  }
  return { ok: true, candidate, inputSnapshot, outputSnapshot, ...hashes };
}
