import { describe, expect, it, vi } from "vitest";

import { fetchXbShared } from "./crossBoardModules.shell";

describe("cross-board in-flight request sharing", () => {
  it("同 fetcher + token 同时挂载只发一次，完成后仍可重新取新数", async () => {
    let resolveFirst: ((value: { generation: number }) => void) | undefined;
    const fetcher = vi.fn(
      () => new Promise<{ generation: number }>((resolve) => { resolveFirst = resolve; }),
    );

    const first = fetchXbShared("token-a", fetcher);
    const second = fetchXbShared("token-a", fetcher);
    await Promise.resolve();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);

    resolveFirst?.({ generation: 1 });
    await expect(first).resolves.toEqual({ generation: 1 });
    await Promise.resolve();

    fetcher.mockResolvedValueOnce({ generation: 2 });
    await expect(fetchXbShared("token-a", fetcher)).resolves.toEqual({ generation: 2 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("不同 token 不合并", async () => {
    const fetcher = vi.fn(async (token: string) => token);
    await Promise.all([fetchXbShared("token-a", fetcher), fetchXbShared("token-b", fetcher)]);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
