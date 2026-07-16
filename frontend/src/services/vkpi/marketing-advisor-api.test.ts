import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();

vi.mock("../http", () => ({
  API_BASE: "",
  ApiResponseError: class ApiResponseError extends Error {
    constructor(_response: Response, payload: unknown) {
      super(String((payload as { detail?: string })?.detail || "api response error"));
    }
  },
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
  buildApiUrl: (path: string) => path,
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  confirmAdvisorMemoryCandidate,
  listAdvisorThreads,
  postAdvisorMessage,
  postAdvisorMessageStream,
  updateAdvisorMemorySettings,
} from "./marketing-advisor-api";

beforeEach(() => {
  apiFetchMock.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("marketing advisor API contract", () => {
  it("clamps the thread limit and unwraps the server envelope", async () => {
    apiFetchMock.mockResolvedValue({ threads: [{ thread_uid: "advthr_1" }] });

    const rows = await listAdvisorThreads("token", 999);

    expect(rows).toEqual([{ thread_uid: "advthr_1" }]);
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/admin/vkpi/marketing-advisor/threads?limit=200",
      { cache: "no-store" },
      "token",
    );
  });

  it("encodes the thread id and sends the server-side idempotency contract", async () => {
    apiFetchMock.mockResolvedValue({ status: "degraded", messages: [] });

    await postAdvisorMessage("token", "thread/with space", "请给建议", "request-1");

    const [path, init, token] = apiFetchMock.mock.calls[0] as [string, { body: string; timeoutMs: number; method: string }, string];
    expect(path).toBe("/api/admin/vkpi/marketing-advisor/threads/thread%2Fwith%20space/messages");
    expect(token).toBe("token");
    expect(init.method).toBe("POST");
    expect(init.timeoutMs).toBe(60_000);
    expect(JSON.parse(init.body)).toEqual({
      content: "请给建议",
      client_request_id: "request-1",
      context_refs: [],
      requested_actions: [],
      allow_external_ai: false,
    });
  });

  it("sends external AI consent only when the caller explicitly opts in", async () => {
    apiFetchMock.mockResolvedValue({ status: "ok", messages: [] });

    await postAdvisorMessage("token", "thread-1", "请给建议", "request-2", true);

    const init = apiFetchMock.mock.calls[0]?.[1] as { body: string };
    expect(JSON.parse(init.body).allow_external_ai).toBe(true);
  });

  it("解析真实 accepted/final SSE，并保留服务端的非 token 流契约", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      [
        "event: accepted\n",
        "data: {\"status\":\"accepted\",\"transport\":\"staged_sse_v1\",\"provider_streaming\":false}\n\n",
        "event: final\n",
        "data: {\"status\":\"degraded\",\"messages\":[]}\n\n",
      ].join(""),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const events: string[] = [];

    const result = await postAdvisorMessageStream(
      "token",
      "thread/with space",
      "请给建议",
      "request-stream-1",
      false,
      (event) => events.push(event.type),
    );

    expect(events).toEqual(["accepted", "final"]);
    expect(result).toEqual({ status: "degraded", messages: [] });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/admin/vkpi/marketing-advisor/threads/thread%2Fwith%20space/messages/stream",
    );
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer token");
    expect(headers.get("Accept")).toBe("text/event-stream");
    expect(JSON.parse(String(init.body))).toMatchObject({
      client_request_id: "request-stream-1",
      allow_external_ai: false,
    });
  });

  it("uses escaped owner-scoped memory paths and omits an absent retention value", async () => {
    apiFetchMock
      .mockResolvedValueOnce({ fact: { fact_uid: "fact-1" } })
      .mockResolvedValueOnce({ settings: { state: "paused", retention_days: 180 } });

    await confirmAdvisorMemoryCandidate("token", "candidate/1");
    await updateAdvisorMemorySettings("token", "paused");

    expect(apiFetchMock.mock.calls[0]?.[0]).toBe(
      "/api/admin/vkpi/marketing-advisor/memory/candidates/candidate%2F1/confirm",
    );
    const settingsInit = apiFetchMock.mock.calls[1]?.[1] as { body: string };
    expect(JSON.parse(settingsInit.body)).toEqual({ state: "paused" });
  });
});
