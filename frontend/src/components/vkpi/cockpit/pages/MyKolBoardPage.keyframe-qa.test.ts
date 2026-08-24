import { beforeEach, describe, expect, it, vi } from "vitest";

const enqueueMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/vkpi/myKolBoard-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/vkpi/myKolBoard-api")>();
  return { ...actual, enqueueMyKolVideoKeyframeQa: (...args: unknown[]) => enqueueMock(...args) };
});

import { runKeyframeQaAction, type KeyframeQaActionDeps } from "./MyKolBoardPage.keyframe-qa";

function deps() {
  const receipts: unknown[] = [];
  const busy: Array<[number, boolean]> = [];
  const refresh = vi.fn();
  const value: KeyframeQaActionDeps = {
    apiToken: "tok",
    target: { poolId: 88, epoch: 2 },
    readOnly: false,
    isCurrent: () => true,
    isBusy: () => false,
    setBusy: (id, on) => busy.push([id, on]),
    setReceipt: (receipt) => receipts.push(receipt),
    refresh,
    writeError: () => "write failed",
  };
  return { value, receipts, busy, refresh };
}

describe("runKeyframeQaAction", () => {
  beforeEach(() => enqueueMock.mockReset());

  it("labels a queue receipt as queued, never as completed", async () => {
    enqueueMock.mockResolvedValue({ status: "queued", provider_calls: false });
    const state = deps();
    await runKeyframeQaAction({ evidence_id: 701 }, state.value);
    expect(enqueueMock).toHaveBeenCalledWith("tok", 88, 701);
    expect(state.receipts.at(-1)).toMatchObject({ tone: "info", text: expect.stringContaining("已排队") });
    expect(String((state.receipts.at(-1) as { text: string }).text)).not.toContain("已完成");
    expect(state.busy).toEqual([[701, true], [701, false]]);
    expect(state.refresh).toHaveBeenCalledOnce();
  });

  it("keeps model or allowance refusal visibly failed without internal model terms", async () => {
    enqueueMock.mockResolvedValue({ status: "ai_disabled", provider_calls: false });
    const state = deps();
    await runKeyframeQaAction({ evidence_id: 702 }, state.value);
    expect(state.receipts.at(-1)).toMatchObject({ tone: "error", text: expect.stringContaining("视频复核模型") });
    expect(JSON.stringify(state.receipts.at(-1))).not.toMatch(/Gemini|LLM|预算闸/i);
  });
});
