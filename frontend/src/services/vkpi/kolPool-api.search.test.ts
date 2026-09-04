import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../http", () => ({
  apiFetch: vi.fn(),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { apiFetch } from "../http";
import {
  approveKolSearchSession,
  createProjectDraftFromSession,
  generateKolSearchSessionOutreach,
  smartKolSearch,
  smartKolSearchProfileAdvanceJob,
} from "./kolPool-api.search";

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
      languages: ["en"],
      profileTypes: ["reviewer"],
      localQualificationSpec: spec,
    });

    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      objective: "prospective_growth",
      candidate_limit: 500,
      limit: 30,
      creator_quota: 15,
      reviewer_quota: 15,
      market: "US",
      platforms: ["youtube"],
      languages: ["en"],
      profile_types: ["reviewer"],
      local_qualification_spec: spec,
      response_projection: "smart_local_compact_v1",
    });
  });

  it("keeps the same hard-filter spec on the queued continuation", async () => {
    const spec = { target_count: 30, unknown_policy: "pending_not_counted" };
    const onlineSpec = { version: "online_net_new_30_v1", target_count: 30 };
    await smartKolSearchProfileAdvanceJob("token", "portrait creators", {
      objective: "existing_evidence",
      candidateLimit: 500,
      limit: 30,
      creatorQuota: 16,
      reviewerQuota: 14,
      advanceLimit: 30,
      languages: ["de", "en"],
      profileTypes: ["creator", "mixed"],
      localQualificationSpec: spec,
      onlineQualificationSpec: onlineSpec,
      sessionId: 701,
      productSku: "AF-35-EVO",
    });

    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      objective: "existing_evidence",
      candidate_limit: 500,
      limit: 30,
      creator_quota: 16,
      reviewer_quota: 14,
      advance_limit: 30,
      languages: ["de", "en"],
      profile_types: ["creator", "mixed"],
      local_qualification_spec: spec,
      online_qualification_spec: onlineSpec,
      session_id: 701,
      product_sku: "AF-35-EVO",
    });
  });

  it("can disable online discovery for an unsupported-platform-only selection without inventing other platforms", async () => {
    await smartKolSearchProfileAdvanceJob("token", "facebook food creators", {
      filters: { platforms: ["facebook"] },
      includeNewDiscovery: false,
      newDiscoveryPlatforms: [],
    });

    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      objective: "prospective_growth",
      filters: { platforms: ["facebook"] },
      include_new_discovery: false,
    });
    expect(body).not.toHaveProperty("new_discovery_platforms");
  });
});

describe("KOL search session approval boundary", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedFetch.mockResolvedValue({} as never);
  });

  it("sends candidate ids only to the explicit approval endpoint", async () => {
    await approveKolSearchSession("token", 51, [11, 12]);
    const body = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    expect(body).toEqual({ kol_pool_ids: [11, 12] });
  });

  it("does not let project draft or outreach bodies override server approvals", async () => {
    await createProjectDraftFromSession("token", 51, { projectName: "Draft" });
    await generateKolSearchSessionOutreach("token", 51, { productName: "Lens" });

    const projectBody = JSON.parse(String(mockedFetch.mock.calls[0][1]?.body));
    const outreachBody = JSON.parse(String(mockedFetch.mock.calls[1][1]?.body));
    expect(projectBody).toEqual({ project_name: "Draft" });
    expect(outreachBody).toEqual({ product_name: "Lens" });
    expect(projectBody).not.toHaveProperty("kol_pool_ids");
    expect(outreachBody).not.toHaveProperty("kol_pool_ids");
  });
});
