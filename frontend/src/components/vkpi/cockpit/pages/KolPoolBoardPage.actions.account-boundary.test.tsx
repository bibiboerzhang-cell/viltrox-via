import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getKolPoolDetailBundle: vi.fn(),
  getKolPoolItem: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => ({
  getKolPoolDetailBundle: (...args: unknown[]) => api.getKolPoolDetailBundle(...args),
  getKolPoolItem: (...args: unknown[]) => api.getKolPoolItem(...args),
}));

vi.mock("../kolPoolRuntime", () => ({
  toCockpitKolPoolRows: (items: unknown[]) => items,
}));

import { usePoolDrawer } from "./KolPoolBoardPage.actions";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const avatarForItem = () => "";
const mergeAvatarSeed = (item: unknown) => item;

beforeEach(() => {
  api.getKolPoolDetailBundle.mockReset();
  api.getKolPoolItem.mockReset().mockRejectedValue(new Error("no fallback"));
});

describe("usePoolDrawer account and request boundaries", () => {
  it("hides account A full detail synchronously when token switches to B", async () => {
    api.getKolPoolDetailBundle.mockResolvedValue({
      item: { id: 1, display_name: "A Creator", email: "account-a@example.com", contact_masked: false },
    });
    const hook = renderHook(
      ({ token }) => usePoolDrawer(token, avatarForItem, mergeAvatarSeed),
      { initialProps: { token: "token-a" } },
    );
    await act(async () => { await hook.result.current.openItem({ id: 1 }); });
    expect(hook.result.current.selectedItem.email).toBe("account-a@example.com");

    hook.rerender({ token: "token-b" });
    expect(hook.result.current.selectedItem).toBeNull();
    expect(JSON.stringify(hook.result.current)).not.toContain("account-a@example.com");
    hook.unmount();
  });

  it("does not let a slower A request overwrite a newer B selection", async () => {
    const first = deferred<any>();
    const second = deferred<any>();
    api.getKolPoolDetailBundle
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const hook = renderHook(() => usePoolDrawer("token", avatarForItem, mergeAvatarSeed));

    let firstOpen: Promise<void>;
    let secondOpen: Promise<void>;
    act(() => {
      firstOpen = hook.result.current.openItem({ id: 1, display_name: "A" });
      secondOpen = hook.result.current.openItem({ id: 2, display_name: "B" });
    });
    await act(async () => {
      second.resolve({ item: { id: 2, display_name: "B", email: "b@example.com", contact_masked: false } });
      await secondOpen!;
    });
    expect(hook.result.current.selectedItem.id).toBe(2);

    await act(async () => {
      first.resolve({ item: { id: 1, display_name: "A", email: "a@example.com", contact_masked: false } });
      await firstOpen!;
    });
    await waitFor(() => expect(hook.result.current.selectedItem.id).toBe(2));
    expect(JSON.stringify(hook.result.current.selectedItem)).not.toContain("a@example.com");
    hook.unmount();
  });
});
