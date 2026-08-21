import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { enqueueKolProfileCrawl } from "./kolPool-api";


beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({ status: "queued", job_id: 9 });
});


describe("enqueueKolProfileCrawl", () => {
  it("uses the fenced canonical-profile endpoint instead of the retired ID-only route", async () => {
    await enqueueKolProfileCrawl(
      "token",
      42,
      "https://www.youtube.com/@Creator",
    );

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init, token] = apiFetch.mock.calls[0] as [
      string,
      { method: string; body: string; timeoutMs: number },
      string,
    ];
    expect(path).toBe("/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue");
    expect(path).not.toContain("/42/enqueue-profile-crawl");
    expect(init.method).toBe("POST");
    expect(init.timeoutMs).toBe(8000);
    expect(JSON.parse(init.body)).toEqual({
      url: "https://www.youtube.com/@Creator",
      kol_pool_id: 42,
      max_posts: 12,
    });
    expect(token).toBe("token");
  });
});
