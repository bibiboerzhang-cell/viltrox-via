import { describe, expect, it } from "vitest";

import {
  hasReviewDetail,
  normalizeSha256,
  parseReviewJsonSnapshot,
  reviewJsonValuesEqual,
  reviewSnapshotSafetyFindings,
  verifyReviewJsonStringHash,
} from "./review-integrity";

describe("review integrity", () => {
  it("直接校验服务端 UTF-8 canonical 字符串，浮点/负零/指数/中文不受 JS 重序列化影响", async () => {
    const snapshot = '{"exponent":1e+20,"negative_zero":-0,"one":1.0,"text":"中文"}';
    const expected = "85a32a97a868d5a20cd8c1622c4109979cf696fa88b5b3817e03048c20921118";
    const valid = await verifyReviewJsonStringHash(snapshot, expected);
    expect(valid).toMatchObject({ valid: true, reason: "ok" });

    const parsed = parseReviewJsonSnapshot(snapshot);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      const row = parsed.value as Record<string, unknown>;
      expect(row.one).toBe(1);
      expect(Object.is(row.negative_zero, -0)).toBe(true);
      expect(row.exponent).toBe(1e20);
      expect(row.text).toBe("中文");
      expect(JSON.stringify(row)).not.toBe(snapshot);
    }

    const mismatch = await verifyReviewJsonStringHash(
      '{"exponent":100000000000000000000,"negative_zero":0,"one":1,"text":"中文"}',
      expected,
    );
    expect(mismatch).toMatchObject({ valid: false, reason: "hash_mismatch" });
  });

  it("hash 可验证但 JSON 无法解析时由解析闸 fail closed", async () => {
    const invalidJson = "not-json";
    const check = await verifyReviewJsonStringHash(
      invalidJson,
      "0c21a879c732a67910d80988df4919d794f6a070aab610ef865032a28046b021",
    );
    expect(check.valid).toBe(true);
    expect(parseReviewJsonSnapshot(invalidJson)).toEqual({ ok: false, reason: "canonical JSON 快照无法解析" });
  });

  it("已解析候选做 JSON 语义比较时不重新序列化并保留负零", () => {
    expect(reviewJsonValuesEqual({ one: 1 }, { one: 1.0 })).toBe(true);
    expect(reviewJsonValuesEqual({ value: -0 }, { value: 0 })).toBe(false);
    expect(reviewJsonValuesEqual({ nested: ["中文", 1e20] }, { nested: ["中文", 100000000000000000000] })).toBe(true);
  });

  it("缺失详情或伪 hash 均 fail closed", () => {
    expect(hasReviewDetail({})).toBe(false);
    expect(hasReviewDetail([])).toBe(false);
    expect(hasReviewDetail({ status: "ok" })).toBe(true);
    expect(normalizeSha256("not-a-hash")).toBeNull();
  });

  it("渲染前拦截 URL query 与凭据字段，且 finding 不回显秘密值", () => {
    const secret = "sk-this-value-must-never-reach-the-dom";
    const findings = reviewSnapshotSafetyFindings({
      preview_url: "https://cdn.example.test/file?X-Amz-Signature=private",
      provider_secret: secret,
      nested: { authorization: "Bearer private-token-value", openaiApiKey: "hidden" },
    });
    expect(findings).toEqual(expect.arrayContaining([
      "$candidate.preview_url: URL query",
      "$candidate.provider_secret: sensitive field",
      "$candidate.nested.authorization: sensitive field",
      "$candidate.nested.openaiApiKey: sensitive field",
    ]));
    expect(findings.join(" ")).not.toContain(secret);
    expect(reviewSnapshotSafetyFindings({ endpoint: "internal:kol-update", url: "https://example.test/public" }))
      .toEqual([]);
  });
});
