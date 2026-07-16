import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());

vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  revokeInventoryQuantityVerification,
  verifyInventoryQuantity,
} from "./inventory-api";

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({ verified: true, quantity_changed: false });
});

describe("inventory quantity truth contract", () => {
  it("serializes source receipt and CAS without a writable quantity field", async () => {
    await verifyInventoryQuantity("token", "AF/35", {
      sourceType: "wms_export",
      sourceRef: "https://warehouse.example/counts/af35.csv?version=3",
      sourceObservedAt: "2026-07-15T11:55:00.000Z",
      evidenceSha256: "a".repeat(64),
      authorizationRef: "WAREHOUSE-77",
      reason: "warehouse count reviewed",
      confirmedByHuman: true,
      expectedId: "inv_1",
      expectedQty: 25,
      expectedRowVersion: 3,
      expectedUpdatedAt: "2026-07-15T12:00:00Z",
    });

    const [path, init, token] = apiFetch.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(path).toBe("/api/admin/vkpi/inventory/AF%2F35/verify");
    expect(token).toBe("token");
    expect(body).toMatchObject({
      source_type: "wms_export",
      evidence_sha256: "a".repeat(64),
      expected_id: "inv_1",
      expected_qty: 25,
      expected_row_version: 3,
      authorization_evidence: {
        authorization_ref: "WAREHOUSE-77",
        confirmed_by_human: true,
      },
    });
    expect(body).not.toHaveProperty("qty");
  });

  it("serializes a CAS-protected revocation without changing quantity", async () => {
    await revokeInventoryQuantityVerification("token", "AF-35", {
      authorizationRef: "WAREHOUSE-REVOKE-77",
      reason: "source artifact was superseded",
      confirmedByHuman: true,
      expectedId: "inv_1",
      expectedQty: 25,
      expectedRowVersion: 4,
      expectedUpdatedAt: "2026-07-15T12:01:00Z",
    });

    const [path, init] = apiFetch.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(path).toBe("/api/admin/vkpi/inventory/AF-35/verification/revoke");
    expect(body.expected_qty).toBe(25);
    expect(body).not.toHaveProperty("qty");
    expect(body.authorization_evidence.authorization_ref).toBe("WAREHOUSE-REVOKE-77");
  });
});
