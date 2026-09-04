import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  favoriteKolPool: vi.fn(),
  listKolPoolFavorites: vi.fn(),
  unfavoriteKolPool: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => ({
  favoriteKolPool: (...args: unknown[]) => api.favoriteKolPool(...args),
  listKolPoolFavorites: (...args: unknown[]) => api.listKolPoolFavorites(...args),
  unfavoriteKolPool: (...args: unknown[]) => api.unfavoriteKolPool(...args),
}));

import { usePoolFavorites } from "./KolPoolBoardPage.actions";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  api.favoriteKolPool.mockReset();
  api.listKolPoolFavorites.mockReset();
  api.unfavoriteKolPool.mockReset();
});

const avatarForItem = () => "";

describe("usePoolFavorites account boundary", () => {
  it("clears A immediately and refuses a late A GET under the same cookie token", async () => {
    const accountAGet = deferred<any>();
    const accountBGet = deferred<any>();
    api.listKolPoolFavorites
      .mockReturnValueOnce(accountAGet.promise)
      .mockReturnValueOnce(accountBGet.promise);

    const hook = renderHook(
      ({ account }) => usePoolFavorites("cookie-session", account, null, vi.fn(), avatarForItem),
      { initialProps: { account: "staff-a" } },
    );
    await waitFor(() => expect(api.listKolPoolFavorites).toHaveBeenCalledTimes(1));

    hook.rerender({ account: "staff-b" });
    expect(hook.result.current.myList.size).toBe(0);
    expect(hook.result.current.syncError).toBe("");
    await waitFor(() => expect(api.listKolPoolFavorites).toHaveBeenCalledTimes(2));

    await act(async () => {
      accountBGet.resolve({ items: [{ kol_pool_id: 202 }] });
      await accountBGet.promise;
    });
    await waitFor(() => expect([...hook.result.current.myList]).toEqual([202]));

    await act(async () => {
      accountAGet.resolve({ items: [{ kol_pool_id: 101 }] });
      await accountAGet.promise;
    });
    expect([...hook.result.current.myList]).toEqual([202]);
    hook.unmount();
  });

  it("does not let a late A mutation overwrite B favorites or B sync state", async () => {
    const accountAMutation = deferred<any>();
    api.listKolPoolFavorites
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ items: [{ kol_pool_id: 202 }] });
    api.favoriteKolPool.mockReturnValueOnce(accountAMutation.promise);

    const setSelectedItem = vi.fn();
    const hook = renderHook(
      ({ account }) => usePoolFavorites(
        "cookie-session",
        account,
        { id: 101, handle: "creator", platform: "youtube" },
        setSelectedItem,
        avatarForItem,
      ),
      { initialProps: { account: "staff-a" } },
    );
    await waitFor(() => expect(hook.result.current.syncState).toBe("synced"));

    act(() => { hook.result.current.toggleMyList(101); });
    expect([...hook.result.current.myList]).toEqual([101]);

    hook.rerender({ account: "staff-b" });
    expect(hook.result.current.myList.size).toBe(0);
    expect(hook.result.current.syncError).toBe("");
    await waitFor(() => expect([...hook.result.current.myList]).toEqual([202]));

    await act(async () => {
      accountAMutation.reject(new Error("private account A failure"));
      await accountAMutation.promise.catch(() => undefined);
    });
    expect([...hook.result.current.myList]).toEqual([202]);
    expect(hook.result.current.syncState).toBe("synced");
    expect(hook.result.current.syncError).toBe("");
    expect(setSelectedItem).not.toHaveBeenCalled();
    hook.unmount();
  });

  it("rejects the original A GET after an A to B to A generation cycle", async () => {
    const originalAGet = deferred<any>();
    api.listKolPoolFavorites
      .mockReturnValueOnce(originalAGet.promise)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ items: [{ kol_pool_id: 303 }] });
    const hook = renderHook(
      ({ account }) => usePoolFavorites("cookie-session", account, null, vi.fn(), avatarForItem),
      { initialProps: { account: "staff-a" } },
    );
    await waitFor(() => expect(api.listKolPoolFavorites).toHaveBeenCalledTimes(1));

    hook.rerender({ account: "staff-b" });
    await waitFor(() => expect(api.listKolPoolFavorites).toHaveBeenCalledTimes(2));
    hook.rerender({ account: "staff-a" });
    await waitFor(() => expect(api.listKolPoolFavorites).toHaveBeenCalledTimes(3));
    await waitFor(() => expect([...hook.result.current.myList]).toEqual([303]));

    await act(async () => {
      originalAGet.resolve({ items: [{ kol_pool_id: 101 }] });
      await originalAGet.promise;
    });
    expect([...hook.result.current.myList]).toEqual([303]);
    hook.unmount();
  });
});
