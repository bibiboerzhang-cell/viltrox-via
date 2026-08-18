import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const serviceMocks = vi.hoisted(() => ({
  approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(),
  favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(),
  resolveKolPool: vi.fn(),
}));

vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { useSmartKolSelection } from "./SmartKolInputPanel.selection";

describe("Smart KOL session-scoped selection artifacts", () => {
  beforeEach(() => {
    Object.values(serviceMocks).forEach((mock) => mock.mockReset());
    serviceMocks.favoriteKolPool.mockResolvedValue({ status: "ok" });
    serviceMocks.approveKolSearchSession.mockResolvedValue({ status: "ok" });
    serviceMocks.createProjectDraftFromSession.mockResolvedValue({ project_uid: "P-1", attached_kol_count: 1 });
    serviceMocks.generateKolSearchSessionOutreach.mockResolvedValue({
      messages: [{ kol_pool_id: 11, body: "draft" }], llm_used: false,
    });
  });

  it("clears notes and generated outreach when the displayed session changes", async () => {
    const { result, rerender } = renderHook(
      ({ sessionId }) => useSmartKolSelection({
        apiToken: "token",
        displayedSearchSessionId: sessionId,
        canApprove: true,
        currentSearchRequest: () => 1,
        isCurrentSearchRequest: () => true,
      }),
      { initialProps: { sessionId: 71 as number | null } },
    );

    act(() => result.current.togglePick(11));
    await act(async () => result.current.addPickedToMyKol());
    act(() => result.current.togglePick(11));
    await act(async () => result.current.approveAndCreateDraft());
    await act(async () => result.current.generateOutreachForPicked());
    expect(result.current.favNote).toContain("已加入");
    expect(result.current.draftNote).toContain("已建草案");
    expect(result.current.outreachNote).toContain("已生成 1 封");
    expect(result.current.outreachResult).not.toBeNull();

    rerender({ sessionId: 72 });
    await waitFor(() => {
      expect(result.current.favNote).toBe("");
      expect(result.current.draftNote).toBe("");
      expect(result.current.outreachNote).toBe("");
      expect(result.current.outreachResult).toBeNull();
      expect([...result.current.pickedIds]).toEqual([]);
    });
  });

  it("does not call approval APIs while the current results are stale or still running", async () => {
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: false,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));
    act(() => result.current.togglePick(11));
    await act(async () => result.current.approveAndCreateDraft());
    await act(async () => result.current.generateOutreachForPicked());
    expect(serviceMocks.approveKolSearchSession).not.toHaveBeenCalled();
    expect(serviceMocks.createProjectDraftFromSession).not.toHaveBeenCalled();
    expect(serviceMocks.generateKolSearchSessionOutreach).not.toHaveBeenCalled();
  });
});
