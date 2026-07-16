import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { deepCrawlKolUrl } from "../../../../services/vkpi/kolPool-api.search";

describe("SmartKolInputPanel profile deep-crawl request contract", () => {
  beforeEach(() => {
    apiFetch.mockReset().mockResolvedValue({ status: "queued" });
  });

  it("sends profile_with_video and two representative videos to the deferred API", async () => {
    await deepCrawlKolUrl("token", "https://www.youtube.com/@ItiJarve", true, {
      maxPosts: 3,
      mode: "profile_with_video",
      representativeVideoLimit: 2,
      deferToQueue: true,
      source: "smart_kol_input_auto",
    });

    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-url-deep-crawl");
    expect(token).toBe("token");
    expect(JSON.parse(String(init.body))).toMatchObject({
      execute: true,
      mode: "profile_with_video",
      representative_video_limit: 2,
      defer_to_queue: true,
      max_posts: 3,
      source: "smart_kol_input_auto",
    });
  });
});
