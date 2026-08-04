import { afterEach, describe, expect, it, vi } from "vitest";
import { askIntelligent, normalizeIntelligentQueryAnswer, queryIntelligent } from "./intelligent-api";

function rawAnswer() {
  return {
    schema_version: "ask_find_v2",
    request_id: "req-top",
    status: "ready",
    intent: "kol.pool.overview",
    answer: "1,594 KOLs",
    facts: [{ key: "total", label: "Total", value: 1594, value_type: "integer", basis: "visible active rows", confidence: "high" }],
    evidence: [{ id: "ev-1", kind: "aggregate", source: "vkpi_kol_pool", confidence: "high" }],
    coverage: { status: "complete", matched_entities: 1594, evidence_count: 1, total_scope: 1594, ratio: 1, notes: [] },
    freshness: { status: "fresh", generated_at: "2026-08-04T12:00:00Z", timezone: "UTC" },
    missing_fields: [],
    actions: [{ type: "navigate", label: "Open KOL Pool", route: "kol-pool", requires_approval: false }],
    trace: {
      request_id: "req-top",
      client_request_id: "client-7",
      thread_id: "ask-find-topbar",
      scope: { mode: "own" },
      mode: "deterministic",
      deterministic: true,
      query_version: "ask_find_v2",
      took_ms: 18,
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("Ask & Find v2 API contract", () => {
  it("posts the exact query contract and normalizes the unified response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(rawAnswer()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await queryIntelligent("secret-token", "How many KOLs?", {
      locale: "en-US",
      threadId: "thread-7",
      scope: "own",
      timeRange: "7d",
      filters: { platform: "YouTube", limit: 30 },
      mode: "deterministic",
      clientRequestId: "client-7",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/admin/vkpi/intelligent/query");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer secret-token");
    expect(JSON.parse(String(init.body))).toEqual({
      query: "How many KOLs?",
      locale: "en-US",
      thread_id: "thread-7",
      scope: "own",
      time_range: "7d",
      filters: { platform: "YouTube", limit: 30 },
      mode: "deterministic",
      client_request_id: "client-7",
    });
    expect(result).toMatchObject({
      request_id: "req-top",
      status: "ready",
      intent: "kol.pool.overview",
      mode: "intent",
      facts: [{ key: "total", value: 1594 }],
      coverage: { matched_entities: 1594, ratio: 1 },
      trace: { client_request_id: "client-7", took_ms: 18 },
    });
  });

  it("sanitizes malformed optional collections instead of leaking arbitrary JSON to UI", () => {
    const result = normalizeIntelligentQueryAnswer({
      status: "partial",
      intent: "unknown",
      answer: "Need more details",
      facts: { unsafe: true },
      evidence: ["raw", { kind: "video", source: "YouTube", extra_secret: "must not survive" }],
      coverage: { status: "partial", matched_entities: "2", evidence_count: "1", total_scope: null, analyzed_count: null, ratio: null, notes: ["ok", 9] },
      freshness: null,
      missing_fields: [null, { field: "topic", reason: "missing", impact: "cannot count" }],
      actions: null,
      trace: {
        request_id: "req-trace",
        took_ms: "31",
        source_status: {
          videos: {
            status: "absent",
            reason: "table_missing",
            result_count: "0",
            count: "0",
            matched_count: "0",
            candidate_count: "12",
            truncated: true,
            time_semantics: "content_publication_event",
            scope: "shared_global",
            required_columns: ["published_at", 9],
            unsafe: "discard me",
          },
          malformed: "raw",
        },
      },
      degraded_reason: "source_timeout",
    });

    expect(result.request_id).toBe("req-trace");
    expect(result.mode).toBe("degraded");
    expect(result.facts).toEqual([]);
    expect(result.evidence[0]).toMatchObject({ kind: "record" });
    expect(result.evidence[1]).toEqual(expect.not.objectContaining({ extra_secret: expect.anything() }));
    expect(result.coverage).toMatchObject({ matched_entities: 2, evidence_count: 1, notes: ["ok"] });
    expect(result.coverage.ratio).toBeUndefined();
    expect(result.coverage.total_scope).toBeUndefined();
    expect(result.coverage.analyzed_count).toBeUndefined();
    expect(result.trace.source_status).toEqual({
      videos: {
        status: "absent",
        reason: "table_missing",
        result_count: 0,
        count: 0,
        matched_count: 0,
        candidate_count: 12,
        truncated: true,
        time_semantics: "content_publication_event",
        scope: "shared_global",
        required_columns: ["published_at"],
      },
    });
    expect(result.missing_fields[1]).toEqual({ field: "topic", reason: "missing", impact: "cannot count" });
  });

  it("keeps absent coverage metrics unknown instead of coercing them to zero", () => {
    const result = normalizeIntelligentQueryAnswer({
      status: "partial",
      coverage: { status: "partial" },
      trace: { source_status: { videos: { status: "absent" } } },
    });

    expect(result.coverage.matched_entities).toBeUndefined();
    expect(result.coverage.evidence_count).toBeUndefined();
  });

  it("keeps the existing Intelligence page on its legacy endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      answer: "legacy answer", mode: "intent", evidence: [], actions: [], cached: false,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await askIntelligent("token", "legacy question");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/vkpi/intelligent/ask");
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({ question: "legacy question" });
    expect(result).toMatchObject({ answer: "legacy answer", mode: "intent" });
  });
});
