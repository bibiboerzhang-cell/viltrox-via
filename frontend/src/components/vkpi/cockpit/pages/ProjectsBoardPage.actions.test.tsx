import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useEndpoint } from "./ProjectsBoardPage.actions";

describe("useEndpoint request lifecycle", () => {
  it("reuses one in-flight read during the React StrictMode effect replay", async () => {
    let resolveRequest!: (value: { ok: boolean }) => void;
    const fetcher = vi.fn(
      () => new Promise<{ ok: boolean }>((resolve) => {
        resolveRequest = resolve;
      }),
    );

    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <React.StrictMode>{children}</React.StrictMode>
    );
    const { result } = renderHook(() => useEndpoint("token", 0, fetcher), { wrapper });

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    resolveRequest({ ok: true });
    await waitFor(() => expect(result.current.data).toEqual({ ok: true }));
    expect(result.current.error).toBe("");
  });

  it("starts a fresh request when version changes", async () => {
    const fetcher = vi.fn(async () => ({ call: 1 }));
    const { rerender } = renderHook(
      ({ version }) => useEndpoint("token", version, fetcher),
      { initialProps: { version: 0 } },
    );

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    rerender({ version: 1 });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("never exposes data resolved for a previous token while the next token read is pending or fails", async () => {
    const pending = new Map<string, {
      resolve: (value: { owner: string }) => void;
      reject: (reason: Error) => void;
    }>();
    const fetcher = vi.fn((token: string) => new Promise<{ owner: string }>((resolve, reject) => {
      pending.set(token, { resolve, reject });
    }));
    const { result, rerender } = renderHook(
      ({ token }) => useEndpoint(token, 0, fetcher),
      { initialProps: { token: "token-a" } },
    );

    await waitFor(() => expect(pending.has("token-a")).toBe(true));
    pending.get("token-a")!.resolve({ owner: "account-a" });
    await waitFor(() => expect(result.current.data).toEqual({ owner: "account-a" }));

    rerender({ token: "token-b" });
    expect(result.current.data).toBeNull();
    await waitFor(() => expect(pending.has("token-b")).toBe(true));
    pending.get("token-b")!.reject(new Error("account-b unavailable"));

    await waitFor(() => expect(result.current.error).toBe("account-b unavailable"));
    expect(result.current.data).toBeNull();
  });
});
