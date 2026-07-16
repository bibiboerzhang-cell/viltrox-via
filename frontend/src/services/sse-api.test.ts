import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./http", () => ({
  API_BASE: "/base",
  apiFetch: vi.fn(async () => ({ status: "ready", expires_in: 30 })),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { apiFetch } from "./http";
import { prepareSseStream, sseEndpointPath } from "./sse-api";

const apiFetchMock = vi.mocked(apiFetch);

describe("SSE one-time ticket API", () => {
  beforeEach(() => apiFetchMock.mockClear());

  it("strips the configured API base before binding the backend endpoint", () => {
    expect(sseEndpointPath("/base/api/admin/vkpi/activity/stream?limit=30"))
      .toBe("/api/admin/vkpi/activity/stream");
  });

  it("sends the login token only in the ticket POST and returns a clean stream URL", async () => {
    const streamUrl = "/base/api/admin/vkpi/activity/stream?limit=30";
    await expect(prepareSseStream(streamUrl, "long-jwt")).resolves.toBe(streamUrl);
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/api/auth/sse-ticket",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ endpoint: "/api/admin/vkpi/activity/stream" }),
      }),
      "long-jwt",
    );
    expect(streamUrl).not.toContain("access_token");
  });
});
