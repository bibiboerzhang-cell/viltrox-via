import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  approveMarketingCost,
  updateMarketingCost,
  voidMarketingCost,
} from "./cost-api";

const authorizationEvidence = {
  authorizationRef: "FIN-CHANGE-77",
  reason: "财务人工核对",
  confirmedByHuman: true,
};

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({});
});

describe("cost lifecycle authorization contract", () => {
  it("serializes fail-closed authorization evidence for update", async () => {
    await updateMarketingCost("token", "19", {
      amountUsd: 12.5,
      note: "财务人工核对",
      authorizationEvidence,
    });

    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/marketing/costs/19");
    expect(token).toBe("token");
    expect(JSON.parse(init.body)).toMatchObject({
      amount_usd: 12.5,
      authorization_evidence: {
        authorization_ref: "FIN-CHANGE-77",
        reason: "财务人工核对",
        confirmed_by_human: true,
      },
    });
  });

  it("serializes the same evidence for approve and void", async () => {
    await approveMarketingCost("token", "19", "批准", authorizationEvidence);
    await voidMarketingCost("token", "20", "重复记录", authorizationEvidence);

    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toMatchObject({
      note: "批准",
      authorization_evidence: { authorization_ref: "FIN-CHANGE-77", confirmed_by_human: true },
    });
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toMatchObject({
      reason: "重复记录",
      authorization_evidence: { authorization_ref: "FIN-CHANGE-77", confirmed_by_human: true },
    });
  });
});
