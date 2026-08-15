import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../http", () => ({
  apiFetch: vi.fn(),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { apiFetch } from "../http";
import { smartKolSearch, smartKolSearchProfileAdvanceJob } from "./kolPool-api.search";

const mockedFetch = vi.mocked(apiFetch);

describe("KOL local qualification request contract", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedFetch.mockResolvedValue({} as never);
  });

  it("passes the server-owned qualification spec on the fast local request", async () => {
    const spec = { target_count: 30, followers_min: 3000, latest_video_max_age_days: 45 };
    await smartKolSearch("token", "portrait creators", {
      candidateLimit: 500,
      limit: 30,
      creatorQuota: 15,
      reviewerQuota: 15,
      market: "US",
      platforms: ["youtube"],
      localQualificationSpec: spec,
    });

    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      candidate_limit: 500,
      limit: 30,
      creator_quota: 15,
      reviewer_quota: 15,
      market: "US",
      platforms: ["youtube"],
      local_qualification_spec: spec,
    });
  });

  it("keeps the same hard-filter spec on the queued continuation", async () => {
    const spec = { target_count: 30, unknown_policy: "pending_not_counted" };
    await smartKolSearchProfileAdvanceJob("token", "portrait creators", {
      candidateLimit: 500,
      limit: 30,
      creatorQuota: 16,
      reviewerQuota: 14,
      advanceLimit: 30,
      localQualificationSpec: spec,
    });

    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      candidate_limit: 500,
      limit: 30,
      creator_quota: 16,
      reviewer_quota: 14,
      advance_limit: 30,
      local_qualification_spec: spec,
    });
  });
});
