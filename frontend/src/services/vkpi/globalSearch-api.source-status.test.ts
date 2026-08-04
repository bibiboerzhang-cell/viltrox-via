import { afterEach, describe, expect, it, vi } from "vitest";
import { globalSearch } from "./globalSearch-api";

afterEach(() => vi.unstubAllGlobals());

describe("global search source truth", () => {
  it("keeps per-source ready/degraded/error/blocked status beside the result arrays", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      q: "26mm",
      kols: [],
      projects: [],
      events: [],
      source_status: {
        kols: { status: "error", result_count: 0, reason: "query_and_fallback_failed" },
        projects: { status: "ready", result_count: 0 },
        events: { status: "blocked", result_count: 0, reason: "visibility_scope_unavailable" },
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    const result = await globalSearch("26mm", { token: "token-1" });

    expect(result.source_status).toEqual({
      kols: { status: "error", result_count: 0, reason: "query_and_fallback_failed" },
      projects: { status: "ready", result_count: 0 },
      events: { status: "blocked", result_count: 0, reason: "visibility_scope_unavailable" },
    });
  });
});
