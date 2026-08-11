import { describe, it, expect, vi, beforeEach } from "vitest";

// W5 recon: 断言 actionInbox-api 对 ../http.apiFetch 的入参(URL/method/body/token)。
// seam = "../http" 的 apiFetch;hermetic(不打真后端)。
const apiFetch = vi.fn();
const jsonBody = (payload: unknown) => JSON.stringify(payload);
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => jsonBody(payload),
}));

import {
  listActionInbox,
  approveAction,
  dismissAction,
  snoozeAction,
  executeAction,
  getActionReviewCandidate,
  reconcileAction,
  verifyActionResult,
} from "./actionInbox-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({});
});

describe("listActionInbox(URL + 默认 limit + 可选过滤)", () => {
  it("默认 limit=50,cache:no-store,带 token", async () => {
    await listActionInbox("tok");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/inbox?limit=50");
    expect(init).toEqual({ cache: "no-store" });
    expect(token).toBe("tok");
  });

  it("透传 limit/category/status 进 query string", async () => {
    await listActionInbox("tok", { limit: 6, category: "kol_profile", status: "suggested" });
    const [path] = apiFetch.mock.calls[0];
    const url = new URL(`http://x${path}`);
    expect(url.searchParams.get("limit")).toBe("6");
    expect(url.searchParams.get("category")).toBe("kol_profile");
    expect(url.searchParams.get("status")).toBe("suggested");
  });

  it("不传 category/status 时不写入这两个 query key", async () => {
    await listActionInbox("tok", { limit: 6 });
    const [path] = apiFetch.mock.calls[0];
    expect(path).not.toContain("category");
    expect(path).not.toContain("status");
  });

  it("返回值原样透传", async () => {
    apiFetch.mockResolvedValueOnce({ items: [{ id: 1 }], available: true, scope: "own" });
    const res = await listActionInbox("tok");
    expect(res.items).toHaveLength(1);
    expect(res.scope).toBe("own");
  });
});

describe("approve / dismiss(POST + 路径含 id + 无 body)", () => {
  it("approve → POST /{id}/approve", async () => {
    await approveAction("tok", 42);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/42/approve");
    expect(init).toEqual({ method: "POST", cache: "no-store" });
    expect(init.body).toBeUndefined();
    expect(token).toBe("tok");
  });

  it("dismiss → POST /{id}/dismiss", async () => {
    await dismissAction("tok", 7);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/7/dismiss");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });
});

describe("snooze(POST + body{minutes} 默认 1440)", () => {
  it("默认 minutes=1440", async () => {
    await snoozeAction("tok", 9);
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/9/snooze");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ minutes: 1440 });
  });

  it("自定义 minutes 透传进 body", async () => {
    await snoozeAction("tok", 9, 30);
    const [, init] = apiFetch.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ minutes: 30 });
  });
});

describe("execute(POST /{id}/execute)", () => {
  it("POST + cache:no-store + token", async () => {
    await executeAction("tok", 5);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/5/execute");
    expect(init).toEqual({ method: "POST", cache: "no-store" });
    expect(token).toBe("tok");
  });
});

describe("reconcile(POST /{id}/reconcile)", () => {
  it("透传人工结论、原因、证据和 correlation id", async () => {
    const payload = {
      decision: "unknown" as const,
      reason: "等待 provider 回执",
      evidence: [{ source: "manual", reference: "log:42" }],
      correlation_id: "action-5-correlation",
    };
    await reconcileAction("tok", 5, payload);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/5/reconcile");
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(init.body)).toEqual(payload);
    expect(token).toBe("tok");
  });
});

describe("verifyActionResult(POST /{id}/verify-result)", () => {
  it("透传经理验收结论、结构化证据和 correlation id", async () => {
    const payload = {
      decision: "accepted" as const,
      reason: "业务结果已由项目回执确认",
      evidence: [{ source: "receipt", type: "reference", reference: "project:VILTROX-42" }],
      correlation_id: "action-review-42-correlation",
      expected_candidate_sha256: "c".repeat(64),
      expected_execution_ledger_id: 91,
      expected_detail_sha256: "a".repeat(64),
    };
    await verifyActionResult("tok", 42, payload);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/actions/42/verify-result");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    expect(JSON.parse(init.body)).toEqual(payload);
    expect(token).toBe("tok");
  });
});

describe("getActionReviewCandidate", () => {
  it("只读获取服务端锁定的候选执行回执", async () => {
    apiFetch.mockResolvedValue({
      action_id: 42,
      execution_ledger_id: 91,
      candidate_canonical_json: '{"execution_ledger_id":91}',
      candidate_sha256: "c".repeat(64),
      detail_json_canonical: '{"outcome":"success"}',
      detail_sha256: "a".repeat(64),
    });
    const result = await getActionReviewCandidate("tok", 42);
    expect(result.detail_json_canonical).toBe('{"outcome":"success"}');
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/actions/42/review-candidate",
      { cache: "no-store" },
      "tok",
    );
  });
});
