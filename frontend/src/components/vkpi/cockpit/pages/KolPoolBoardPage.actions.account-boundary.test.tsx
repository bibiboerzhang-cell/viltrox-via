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

import {
  recallProductScopeForDrawer,
  usePoolDrawer,
  useSmartOpeners,
} from "./KolPoolBoardPage.actions";

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

  it("hides account A and rejects its late detail when account id changes under one cookie token", async () => {
    const accountADetail = deferred<any>();
    const accountBDetail = deferred<any>();
    api.getKolPoolDetailBundle
      .mockReturnValueOnce(accountADetail.promise)
      .mockReturnValueOnce(accountBDetail.promise);
    const hook = renderHook(
      ({ account }) => usePoolDrawer("cookie-session", avatarForItem, mergeAvatarSeed, account),
      { initialProps: { account: "staff-a" } },
    );

    let accountAOpen!: Promise<void>;
    act(() => { accountAOpen = hook.result.current.openItem({ id: 1, display_name: "A" }); });
    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({ id: 1 }));

    hook.rerender({ account: "staff-b" });
    expect(hook.result.current.selectedItem).toBeNull();
    expect(hook.result.current.selectedDetailBundle).toBeNull();

    let accountBOpen!: Promise<void>;
    act(() => { accountBOpen = hook.result.current.openItem({ id: 2, display_name: "B" }); });
    await act(async () => {
      accountBDetail.resolve({ item: { id: 2, display_name: "B", email: "b@example.com" } });
      await accountBOpen;
    });
    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      id: 2,
      email: "b@example.com",
    }));

    await act(async () => {
      accountADetail.resolve({ item: { id: 1, display_name: "A", email: "private-a@example.com" } });
      await accountAOpen;
    });
    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      id: 2,
      email: "b@example.com",
    }));
    expect(JSON.stringify(hook.result.current)).not.toContain("private-a@example.com");
    hook.unmount();
  });

  it("rejects an original A response after an A to B to A account cycle", async () => {
    const originalA = deferred<any>();
    const currentA = deferred<any>();
    api.getKolPoolDetailBundle
      .mockReturnValueOnce(originalA.promise)
      .mockReturnValueOnce(currentA.promise);
    const hook = renderHook(
      ({ account }) => usePoolDrawer("cookie-session", avatarForItem, mergeAvatarSeed, account),
      { initialProps: { account: "staff-a" } },
    );

    let originalOpen!: Promise<void>;
    act(() => { originalOpen = hook.result.current.openItem({ id: 1 }); });
    hook.rerender({ account: "staff-b" });
    hook.rerender({ account: "staff-a" });

    let currentOpen!: Promise<void>;
    act(() => { currentOpen = hook.result.current.openItem({ id: 3 }); });
    await act(async () => {
      currentA.resolve({ item: { id: 3, email: "current-a@example.com" } });
      await currentOpen;
    });
    await act(async () => {
      originalA.resolve({ item: { id: 1, email: "stale-a@example.com" } });
      await originalOpen;
    });

    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      id: 3,
      email: "current-a@example.com",
    }));
    expect(JSON.stringify(hook.result.current)).not.toContain("stale-a@example.com");
    hook.unmount();
  });
});

describe("usePoolDrawer current-search product scope", () => {
  it("keeps the current family scope over historical SKU rows in bundle and reload", async () => {
    const familyContext = {
      kind: "product_family",
      identity: "family:Viltrox 35mm F1.2 family",
      label: "Viltrox 35mm F1.2 family",
    };
    api.getKolPoolDetailBundle
      .mockResolvedValueOnce({
        status: "ready",
        item: {
          id: 51,
          display_name: "Server Creator",
          product_context: { kind: "catalog_sku", sku: "OLD-SERVER-SKU" },
          product_sku: "OLD-SERVER-SKU",
          productSku: "OLDER-CAMEL-SKU",
        },
      })
      .mockResolvedValueOnce({
        status: "ready",
        item: {
          id: 51,
          display_name: "Reloaded Creator",
          product_sku: "RELOAD-OLD-SKU",
          productSku: "RELOAD-OLD-CAMEL-SKU",
        },
      });
    const hook = renderHook(() => usePoolDrawer("token", avatarForItem, mergeAvatarSeed));

    await act(async () => {
      await hook.result.current.openItem({ id: 51, product_context: familyContext });
    });

    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      id: 51,
      display_name: "Server Creator",
      product_context: familyContext,
      product_identity: familyContext.identity,
      product_family: familyContext.identity,
      product_family_name: familyContext.label,
    }));
    expect(hook.result.current.selectedItem).not.toHaveProperty("product_sku");
    expect(hook.result.current.selectedItem).not.toHaveProperty("productSku");
    expect(hook.result.current.selectedDetailBundle.item).not.toHaveProperty("product_sku");
    expect(hook.result.current.selectedDetailBundle.item).not.toHaveProperty("productSku");

    await act(async () => { await hook.result.current.reloadDetail(); });

    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      display_name: "Reloaded Creator",
      product_context: familyContext,
      product_family: familyContext.identity,
    }));
    expect(hook.result.current.selectedItem).not.toHaveProperty("product_sku");
    expect(hook.result.current.selectedItem).not.toHaveProperty("productSku");
    expect(hook.result.current.selectedDetailBundle.item).not.toHaveProperty("product_sku");
    expect(hook.result.current.selectedDetailBundle.item).not.toHaveProperty("productSku");
    hook.unmount();
  });

  it("keeps the current exact SKU over fallback detail and clears historical family scope", async () => {
    const exactContext = {
      kind: "catalog_sku",
      identity: "AF-35MM-F12-LAB-FE",
      label: "AF 35mm F1.2 LAB FE",
      sku: "AF-35MM-F12-LAB-FE",
    };
    api.getKolPoolDetailBundle.mockRejectedValueOnce(new Error("bundle unavailable"));
    api.getKolPoolItem.mockResolvedValueOnce({
      item: {
        id: 52,
        display_name: "Fallback Creator",
        product_context: { kind: "product_family", identity: "family:OLD" },
        product_sku: "OLD-SNAKE-SKU",
        productSku: "OLD-CAMEL-SKU",
        product_family: "family:OLD",
        product_family_name: "Old family",
        productFamily: "family:OLDER",
        productFamilyName: "Older family",
      },
      freshness: { state: "fresh" },
    });
    const hook = renderHook(() => usePoolDrawer("token", avatarForItem, mergeAvatarSeed));

    await act(async () => {
      await hook.result.current.openItem({ id: 52, product_context: exactContext });
    });

    expect(hook.result.current.selectedItem).toEqual(expect.objectContaining({
      id: 52,
      display_name: "Fallback Creator",
      product_context: exactContext,
      product_sku: exactContext.sku,
      product_identity: exactContext.identity,
      product_name: exactContext.label,
      freshness: { state: "fresh" },
    }));
    expect(hook.result.current.selectedItem).not.toHaveProperty("productSku");
    expect(hook.result.current.selectedItem).not.toHaveProperty("product_family");
    expect(hook.result.current.selectedItem).not.toHaveProperty("product_family_name");
    expect(hook.result.current.selectedItem).not.toHaveProperty("productFamily");
    expect(hook.result.current.selectedItem).not.toHaveProperty("productFamilyName");
    hook.unmount();
  });
});

describe("smart-search detail product context", () => {
  it.each([
    ["name-only", { product_name: "A guessed monitor" }],
    ["family-name-only", { product_family_name: "A guessed family" }],
    ["context-label-only", { product_context: { label: "A guessed label" } }],
    ["context-kind-only", { product_context: { kind: "catalog_sku" } }],
  ])("does not let %s primary metadata discard the exact DC-X2 fallback", (_label, weakPrimary) => {
    const exactFallback = {
      product_context: {
        kind: "catalog_sku",
        identity: "DC-X2",
        sku: "DC-X2",
        label: "DC-X2 Monitor",
      },
      product_sku: "DC-X2",
      product_identity: "DC-X2",
      product_name: "DC-X2 Monitor",
    };

    expect(recallProductScopeForDrawer(weakPrimary, exactFallback)).toEqual(exactFallback);
  });

  it("selects an exact primary scope as a whole instead of backfilling fallback family fields", () => {
    const scope = recallProductScopeForDrawer(
      { productSku: "AF-NEW-EXACT" },
      {
        product_context: { kind: "product_family", identity: "family:OLD", label: "Old family" },
        product_family: "family:OLD",
        product_family_name: "Old family",
        product_name: "Old fallback product",
      },
    );

    expect(scope).toEqual({
      product_sku: "AF-NEW-EXACT",
      product_identity: "AF-NEW-EXACT",
    });
    expect(scope).not.toHaveProperty("product_context");
    expect(scope).not.toHaveProperty("product_family");
    expect(scope).not.toHaveProperty("product_family_name");
    expect(scope).not.toHaveProperty("productSku");
  });

  it("selects a camel-case primary family as a whole instead of backfilling fallback exact fields", () => {
    const scope = recallProductScopeForDrawer(
      { productFamily: "family:NEW", productFamilyName: "New family" },
      {
        product_context: { kind: "catalog_sku", identity: "OLD-SKU", sku: "OLD-SKU" },
        product_sku: "OLD-SKU",
        product_name: "Old exact product",
      },
    );

    expect(scope).toEqual({
      product_identity: "family:NEW",
      product_family: "family:NEW",
      product_family_name: "New family",
    });
    expect(scope).not.toHaveProperty("product_context");
    expect(scope).not.toHaveProperty("product_sku");
    expect(scope).not.toHaveProperty("productFamily");
    expect(scope).not.toHaveProperty("productFamilyName");
  });

  it("adopts and normalizes the fallback scope only when primary has no product scope", () => {
    expect(recallProductScopeForDrawer(
      { id: 99, display_name: "No-scope primary" },
      {
        productFamily: "family:FALLBACK",
        productFamilyName: "Fallback family",
        productName: "Fallback family product",
      },
    )).toEqual({
      product_identity: "family:FALLBACK",
      product_family: "family:FALLBACK",
      product_family_name: "Fallback family",
      product_name: "Fallback family product",
    });
  });

  it("keeps a family identity through the pool-detail opener without fabricating product_sku", () => {
    const openItem = vi.fn().mockResolvedValue(undefined);
    const hook = renderHook(() => useSmartOpeners(
      "token",
      [{
        id: 42,
        display_name: "Family Creator",
        product_sku: "OLD-HISTORICAL-SKU",
        productSku: "OLD-HISTORICAL-CAMEL-SKU",
        productFamily: "family:OLD-CAMEL",
        productFamilyName: "Old camel family",
      }],
      openItem,
      mergeAvatarSeed,
    ));

    act(() => hook.result.current.openRecallItem({
      kol_pool_id: 42,
      handle: "family_creator",
      product_context: {
        kind: "product_family",
        identity: "family:Viltrox 35mm F1.2 family",
        label: "Viltrox 35mm F1.2 family",
      },
      product_identity: "family:Viltrox 35mm F1.2 family",
      product_family: "family:Viltrox 35mm F1.2 family",
      product_family_name: "Viltrox 35mm F1.2 family",
      product_name: "Viltrox 35mm F1.2 family",
    } as any));

    expect(openItem).toHaveBeenCalledWith(expect.objectContaining({
      id: 42,
      product_family_name: "Viltrox 35mm F1.2 family",
      product_identity: "family:Viltrox 35mm F1.2 family",
    }));
    const opened = openItem.mock.calls[0][0];
    expect(opened).not.toHaveProperty("product_sku");
    expect(opened).not.toHaveProperty("productSku");
    expect(opened).not.toHaveProperty("productFamily");
    expect(opened).not.toHaveProperty("productFamilyName");
    hook.unmount();
  });

  it("lets the current exact SKU replace stale family metadata on the pool row", () => {
    const openItem = vi.fn().mockResolvedValue(undefined);
    const hook = renderHook(() => useSmartOpeners(
      "token",
      [{
        id: 43,
        display_name: "Exact Creator",
        productSku: "OLD-CAMEL-SKU",
        product_family: "family:OLD",
        product_family_name: "Old family",
        productFamily: "family:OLD-CAMEL",
        productFamilyName: "Old camel family",
      }],
      openItem,
      mergeAvatarSeed,
    ));

    act(() => hook.result.current.openRecallItem({
      kol_pool_id: 43,
      handle: "exact_creator",
      product_context: {
        kind: "catalog_sku",
        identity: "AF-35MM-F12-LAB-FE",
        label: "AF 35mm F1.2 LAB FE",
        sku: "AF-35MM-F12-LAB-FE",
      },
      product_identity: "AF-35MM-F12-LAB-FE",
      product_sku: "AF-35MM-F12-LAB-FE",
      product_name: "AF 35mm F1.2 LAB FE",
    } as any));

    expect(openItem).toHaveBeenCalledWith(expect.objectContaining({
      id: 43,
      product_sku: "AF-35MM-F12-LAB-FE",
      product_identity: "AF-35MM-F12-LAB-FE",
    }));
    expect(openItem.mock.calls[0][0]).not.toHaveProperty("product_family");
    expect(openItem.mock.calls[0][0]).not.toHaveProperty("product_family_name");
    expect(openItem.mock.calls[0][0]).not.toHaveProperty("productSku");
    expect(openItem.mock.calls[0][0]).not.toHaveProperty("productFamily");
    expect(openItem.mock.calls[0][0]).not.toHaveProperty("productFamilyName");
    hook.unmount();
  });

  it("keeps an ordinary profile opener unchanged when the profile result has no product scope", () => {
    const openItem = vi.fn().mockResolvedValue(undefined);
    const hook = renderHook(() => useSmartOpeners(
      "token",
      [{
        id: 44,
        display_name: "Stored Creator",
      }],
      openItem,
      mergeAvatarSeed,
    ));

    act(() => hook.result.current.openProfileItem({
      matched_kol_pool_id: 44,
      profile_flow: { profile_data: { handle: "stored_creator", platform: "youtube" } },
    } as any));

    expect(openItem).toHaveBeenCalledWith(expect.objectContaining({
      id: 44,
      handle: "stored_creator",
    }));
    const opened = openItem.mock.calls[0][0];
    expect(opened).not.toHaveProperty("product_context");
    expect(opened).not.toHaveProperty("product_sku");
    expect(opened).not.toHaveProperty("productSku");
    expect(opened).not.toHaveProperty("product_family");
    expect(opened).not.toHaveProperty("productFamily");
    hook.unmount();
  });
});
