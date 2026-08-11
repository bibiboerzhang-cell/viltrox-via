export type ReviewHashCheck = {
  valid: boolean;
  expected: string;
  actual: string | null;
  reason: "ok" | "invalid_snapshot_json" | "invalid_expected_hash" | "web_crypto_unavailable" | "hash_mismatch";
};

export function reviewHashReasonLabel(reason: ReviewHashCheck["reason"]): string {
  if (reason === "invalid_snapshot_json") return "服务端 canonical JSON 快照缺失";
  if (reason === "invalid_expected_hash") return "服务端返回的 SHA-256 格式无效";
  if (reason === "web_crypto_unavailable") return "当前浏览器无法执行 SHA-256 校验";
  if (reason === "hash_mismatch") return "展示详情与服务端 SHA-256 不一致";
  return "详情与 SHA-256 一致";
}

export function normalizeSha256(value: unknown): string | null {
  const hash = String(value || "").trim().toLowerCase();
  return /^[a-f0-9]{64}$/.test(hash) ? hash : null;
}

export function hasReviewDetail(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value && typeof value === "object" && Object.keys(value).length > 0);
}

const SENSITIVE_KEY = /(?:^|[_-])(?:authorization|api[_-]?key|access[_-]?(?:key|token)|refresh[_-]?token|client[_-]?secret|password|passwd|secret|signature|signed[_-]?url|provider[_-]?(?:key|secret|token))(?:$|[_-])/i;
const QUERY_URL = /(?:https?:\/\/|\/)[^\s"']*\?[^\s"']+/i;
const SECRET_VALUE = /(?:\bBearer\s+[A-Za-z0-9._~+\/-]{8,}|\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|signature)=[^\s&]+|\bX-Amz-(?:Credential|Signature|Security-Token)=[^\s&]+|\b(?:sk|rk)-[A-Za-z0-9_-]{12,}\b|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)/i;

/**
 * 候选接口应先在服务端脱敏；这里再做一道只读展示闸，避免意外把签名 URL、
 * query 或 Provider 凭据写进 DOM。只返回字段路径/类别，不回显可疑值本身。
 */
export function reviewSnapshotSafetyFindings(value: unknown): string[] {
  const findings: string[] = [];
  const seen = new WeakSet<object>();

  const visit = (current: unknown, path: string) => {
    if (typeof current === "string") {
      if (QUERY_URL.test(current)) findings.push(`${path}: URL query`);
      if (SECRET_VALUE.test(current)) findings.push(`${path}: credential`);
      return;
    }
    if (!current || typeof current !== "object") return;
    if (seen.has(current)) {
      findings.push(`${path}: circular reference`);
      return;
    }
    seen.add(current);
    if (Array.isArray(current)) {
      current.forEach((item, index) => visit(item, `${path}[${index}]`));
      return;
    }
    Object.entries(current as Record<string, unknown>).forEach(([key, item]) => {
      const childPath = `${path}.${key}`;
      const normalizedKey = key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
      if (SENSITIVE_KEY.test(normalizedKey) || /(?:^|[_-])(?:token|credential)$/.test(normalizedKey)) {
        findings.push(`${childPath}: sensitive field`);
        return;
      }
      visit(item, childPath);
    });
  };

  visit(value, "$candidate");
  return Array.from(new Set(findings));
}

export function parseReviewJsonSnapshot(value: unknown): { ok: true; value: unknown } | { ok: false; reason: string } {
  if (typeof value !== "string" || !value.trim()) return { ok: false, reason: "canonical JSON 快照缺失" };
  try {
    return { ok: true, value: JSON.parse(value) };
  } catch {
    return { ok: false, reason: "canonical JSON 快照无法解析" };
  }
}

/** 比较两个已解析 JSON 值，不经过 stringify；保留 -0 等 JSON 数字边界。 */
export function reviewJsonValuesEqual(left: unknown, right: unknown): boolean {
  if (typeof left === "number" || typeof right === "number") return Object.is(left, right);
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => reviewJsonValuesEqual(item, right[index]));
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort();
  const rightKeys = Object.keys(rightRecord).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && reviewJsonValuesEqual(leftRecord[key], rightRecord[key]));
}

/** 直接验证服务端 canonical UTF-8 JSON 字符串；严禁重新序列化对象后验 hash。 */
export async function verifyReviewJsonStringHash(
  canonicalJson: unknown,
  expectedValue: unknown,
): Promise<ReviewHashCheck> {
  if (typeof canonicalJson !== "string" || !canonicalJson.trim()) {
    return { valid: false, expected: String(expectedValue || ""), actual: null, reason: "invalid_snapshot_json" };
  }
  const expected = normalizeSha256(expectedValue);
  if (!expected) {
    return { valid: false, expected: String(expectedValue || ""), actual: null, reason: "invalid_expected_hash" };
  }
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    return { valid: false, expected, actual: null, reason: "web_crypto_unavailable" };
  }
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(canonicalJson));
  const actual = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return {
    valid: actual === expected,
    expected,
    actual,
    reason: actual === expected ? "ok" : "hash_mismatch",
  };
}
