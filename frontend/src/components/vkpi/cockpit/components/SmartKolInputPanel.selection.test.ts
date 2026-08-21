import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const serviceMocks = vi.hoisted(() => ({
  approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(),
  favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(),
  listKolPoolFavorites: vi.fn(),
  resolveKolPool: vi.fn(),
}));

vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { useSmartKolSelection } from "./SmartKolInputPanel.selection";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("Smart KOL session-scoped selection artifacts", () => {
  beforeEach(() => {
    Object.values(serviceMocks).forEach((mock) => mock.mockReset());
    serviceMocks.favoriteKolPool.mockResolvedValue({ status: "favorited" });
    serviceMocks.listKolPoolFavorites.mockResolvedValue({ items: [], total: 0 });
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
        canFavorite: true,
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
    expect(result.current.favNote).toContain("处理完成");
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
      canFavorite: true,
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

  it("does not write MY KOL from a stale restored result", async () => {
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: false,
      canFavorite: false,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));
    act(() => result.current.togglePick(11));
    await act(async () => result.current.addPickedToMyKol());
    await act(async () => result.current.favoriteOne(11));
    expect(serviceMocks.favoriteKolPool).not.toHaveBeenCalled();
  });

  it("hydrates existing MY KOL status and keeps repeated follow idempotent in the UI", async () => {
    serviceMocks.listKolPoolFavorites.mockResolvedValue({
      items: [{ kol_pool_id: 11, note: "must not render" }],
      total: 1,
    });
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: true,
      canFavorite: true,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));

    await waitFor(() => expect(result.current.favoriteIds.has(11)).toBe(true));
    await act(async () => result.current.favoriteOne(11));
    expect(serviceMocks.favoriteKolPool).not.toHaveBeenCalled();
    expect(result.current.favoriteResults.get(11)).toBe("已在 MY KOL");
  });

  it("shows per-row failure, retries, and then suppresses another duplicate write", async () => {
    serviceMocks.favoriteKolPool
      .mockRejectedValueOnce(new Error("provider detail must not render"))
      .mockResolvedValueOnce({ status: "favorited", kol_pool_id: 12 });
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: true,
      canFavorite: true,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));

    await waitFor(() => expect(result.current.favoritesSyncing).toBe(false));
    await act(async () => result.current.favoriteOne(12));
    expect(result.current.favoriteErrors.get(12)).toBe("关注失败，请重试");
    expect(result.current.favNote).not.toContain("provider detail");

    await act(async () => result.current.favoriteOne(12));
    expect(result.current.favoriteIds.has(12)).toBe(true);
    expect(result.current.favoriteErrors.has(12)).toBe(false);
    expect(result.current.favoriteResults.get(12)).toBe("已加入 MY KOL");

    await act(async () => result.current.favoriteOne(12));
    expect(serviceMocks.favoriteKolPool).toHaveBeenCalledTimes(2);
  });

  it("classifies a server already_favorited response as existing rather than newly added", async () => {
    serviceMocks.favoriteKolPool.mockResolvedValueOnce({ status: "already_favorited", kol_pool_id: 14 });
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: true,
      canFavorite: true,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));

    await waitFor(() => expect(result.current.favoritesSyncing).toBe(false));
    await act(async () => result.current.favoriteOne(14));
    expect(result.current.favoriteIds.has(14)).toBe(true);
    expect(result.current.favoriteResults.get(14)).toBe("已在 MY KOL");
    expect(result.current.favNote).toContain("已在你的 MY KOL");
    expect(result.current.favNote).not.toContain("已关注 1 人");
  });

  it("drops a single-row completion from an obsolete request epoch", async () => {
    const pending = deferred<{ status: string; kol_pool_id: number }>();
    serviceMocks.favoriteKolPool.mockReturnValueOnce(pending.promise);
    let epoch = 1;
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: true,
      canFavorite: true,
      currentSearchRequest: () => epoch,
      isCurrentSearchRequest: (candidate) => candidate === epoch,
    }));

    await waitFor(() => expect(result.current.favoritesSyncing).toBe(false));
    let action!: Promise<void>;
    act(() => { action = result.current.favoriteOne(21); });
    await waitFor(() => expect(result.current.favoriteBusyIds.has(21)).toBe(true));
    epoch = 2;
    await act(async () => {
      pending.resolve({ status: "favorited", kol_pool_id: 21 });
      await action;
    });

    expect(result.current.favoriteIds.has(21)).toBe(false);
    expect(result.current.favoriteResults.has(21)).toBe(false);
    expect(result.current.favoriteErrors.has(21)).toBe(false);
    expect(result.current.favNote).toBe("");
    expect(result.current.favoriteBusyIds.has(21)).toBe(false);
  });

  it("does not let an old bulk completion overwrite the next session selection", async () => {
    const pending = deferred<{ status: string; kol_pool_id: number }>();
    serviceMocks.favoriteKolPool.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(
      ({ sessionId }) => useSmartKolSelection({
        apiToken: "token",
        displayedSearchSessionId: sessionId,
        canApprove: true,
        canFavorite: true,
        currentSearchRequest: () => 1,
        isCurrentSearchRequest: () => true,
      }),
      { initialProps: { sessionId: 71 } },
    );

    await waitFor(() => expect(result.current.favoritesSyncing).toBe(false));
    act(() => result.current.setPickedIds(new Set([31])));
    let action!: Promise<void>;
    act(() => { action = result.current.addPickedToMyKol(); });
    await waitFor(() => expect(result.current.addingFav).toBe(true));

    rerender({ sessionId: 72 });
    await waitFor(() => expect(result.current.addingFav).toBe(false));
    act(() => result.current.setPickedIds(new Set([99])));
    await act(async () => {
      pending.resolve({ status: "favorited", kol_pool_id: 31 });
      await action;
    });

    expect([...result.current.pickedIds]).toEqual([99]);
    expect(result.current.favoriteIds.has(31)).toBe(false);
    expect(result.current.favoriteResults.has(31)).toBe(false);
    expect(result.current.favoriteErrors.has(31)).toBe(false);
    expect(result.current.favNote).toBe("");
  });

  it("keeps only failed rows selected after a partial bulk follow", async () => {
    serviceMocks.favoriteKolPool
      .mockResolvedValueOnce({ status: "already_favorited", kol_pool_id: 11 })
      .mockResolvedValueOnce({ status: "favorited", kol_pool_id: 12 })
      .mockRejectedValueOnce(new Error("failed"));
    const { result } = renderHook(() => useSmartKolSelection({
      apiToken: "token",
      displayedSearchSessionId: 71,
      canApprove: true,
      canFavorite: true,
      currentSearchRequest: () => 1,
      isCurrentSearchRequest: () => true,
    }));

    await waitFor(() => expect(result.current.favoritesSyncing).toBe(false));
    act(() => result.current.setPickedIds(new Set([11, 12, 13])));
    await act(async () => result.current.addPickedToMyKol());

    expect(result.current.favoriteIds.has(11)).toBe(true);
    expect(result.current.favoriteIds.has(12)).toBe(true);
    expect([...result.current.pickedIds]).toEqual([13]);
    expect(result.current.favNote).toContain("新增 1 人");
    expect(result.current.favNote).toContain("已关注 1 人");
    expect(result.current.favNote).toContain("失败或未确认 1 人");
    expect(result.current.favoriteResults.get(11)).toBe("已在 MY KOL");
    expect(result.current.favoriteResults.get(12)).toBe("已加入 MY KOL");
  });
});
