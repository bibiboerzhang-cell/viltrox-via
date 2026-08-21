import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getKolPoolContentFit = vi.fn();
const analyzeKolPoolContentFit = vi.fn();

vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolPoolContentFit: (...args: unknown[]) => getKolPoolContentFit(...args),
  analyzeKolPoolContentFit: (...args: unknown[]) => analyzeKolPoolContentFit(...args),
}));

import {
  CONTENT_FIT_POLL_TIMEOUT_MS,
  contentFitSnapshot,
  useKolContentFitState,
} from "./useKolContentFitState";

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function renderState(kolPoolId: number | string = 42) {
  return renderHook(
    ({ id }) => useKolContentFitState({
      apiToken: "token",
      kolPoolId: id,
      productSku: "AF-35-PRO",
      canAnalyze: true,
    }),
    { initialProps: { id: kolPoolId } },
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-21T12:00:00Z"));
  getKolPoolContentFit.mockReset().mockResolvedValue({ state: "missing", status: "not_requested" });
  analyzeKolPoolContentFit.mockReset().mockResolvedValue({ state: "queued", status: "queued", job_id: 91 });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("content-fit durable status", () => {
  it("recognises retrying and preserves bounded stage/reason", () => {
    const snapshot = contentFitSnapshot({
      state: "retrying",
      job_id: 91,
      analysis_job: {
        id: 91,
        state: "retrying",
        stage: "provider_generation",
        reason: "provider_429",
      },
    });

    expect(snapshot.active).toBe(true);
    expect(snapshot.terminal).toBe(false);
    expect(snapshot.stage).toBe("provider_generation");
    expect(snapshot.reason).toBe("provider_429");
  });

  it("treats normal queued POST as active and polls to ready", async () => {
    getKolPoolContentFit
      .mockResolvedValueOnce({ state: "missing", status: "not_requested" })
      .mockResolvedValueOnce({ state: "ready", status: "ready", result: { fit_verdict: "fit" } });
    const view = renderState();
    await flush();

    act(() => view.result.current.handleContentFitAnalyze(false));
    await flush();

    expect(view.result.current.contentFitBusy).toBe(true);
    expect(view.result.current.contentFitError).toContain("已排队");
    expect(view.result.current.contentFitError).not.toContain("LLM");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(view.result.current.contentFitBusy).toBe(false);
    expect(view.result.current.contentFit?.result.fit_verdict).toBe("fit");
    expect(getKolPoolContentFit).toHaveBeenLastCalledWith(
      "token",
      42,
      { productSku: "AF-35-PRO", jobId: 91 },
    );
  });

  it("recovers an active job on mount and surfaces terminal stage/reason", async () => {
    getKolPoolContentFit
      .mockResolvedValueOnce({
        state: "running",
        job_id: 91,
        analysis_job: { id: 91, state: "running", stage: "content_fit" },
      })
      .mockResolvedValueOnce({
        state: "blocked",
        status: "blocked",
        job_id: 91,
        analysis_job: { id: 91, state: "blocked", stage: "scope_revalidation", reason: "permission_revoked" },
      });
    const view = renderState();
    await flush();

    expect(view.result.current.contentFitBusy).toBe(true);
    expect(view.result.current.contentFitError).toContain("处理中");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(view.result.current.contentFitBusy).toBe(false);
    expect(view.result.current.contentFitError).toContain("scope_revalidation");
    expect(view.result.current.contentFitError).toContain("permission_revoked");
  });

  it("shows stale cached content without claiming ready", async () => {
    getKolPoolContentFit.mockResolvedValueOnce({
      state: "stale",
      status: "stale",
      result: { fit_verdict: "partial_fit" },
    });
    const view = renderState();
    await flush();

    expect(view.result.current.contentFit?.result.fit_verdict).toBe("partial_fit");
    expect(view.result.current.contentFitBusy).toBe(false);
    expect(view.result.current.contentFitError).toContain("已过期");
  });

  it("drops a late response after switching KOL", async () => {
    let resolveOld: (value: unknown) => void = () => undefined;
    const oldRequest = new Promise((resolve) => { resolveOld = resolve; });
    getKolPoolContentFit.mockImplementation((_token, id) => (
      String(id) === "42"
        ? oldRequest
        : Promise.resolve({ state: "ready", status: "ready", result: { fit_verdict: "fit-new" } })
    ));
    const view = renderState(42);

    view.rerender({ id: 43 });
    await flush();
    expect(view.result.current.contentFit?.result.fit_verdict).toBe("fit-new");

    resolveOld({ state: "ready", status: "ready", result: { fit_verdict: "stale-old" } });
    await flush();

    expect(view.result.current.contentFit?.result.fit_verdict).toBe("fit-new");
  });

  it("stops at the bounded timeout without reporting failure", async () => {
    getKolPoolContentFit
      .mockResolvedValueOnce({ state: "missing", status: "not_requested" })
      .mockResolvedValue({ state: "queued", status: "queued", job_id: 91 });
    const view = renderState();
    await flush();
    act(() => view.result.current.handleContentFitAnalyze(false));
    await flush();

    vi.setSystemTime(new Date(Date.now() + CONTENT_FIT_POLL_TIMEOUT_MS));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(view.result.current.contentFitBusy).toBe(false);
    expect(view.result.current.contentFitError).toContain("仍在后台进行");
    expect(view.result.current.contentFitError).not.toContain("失败");
  });
});
