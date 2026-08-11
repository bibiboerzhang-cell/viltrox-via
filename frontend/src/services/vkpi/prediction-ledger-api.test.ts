import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { recordPredictionActual } from "./prediction-ledger-api";

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ ok: true, id: 18, deduped: false });
});

describe("recordPredictionActual", () => {
  it("只发送 outcome 证据绑定，不允许客户端提交 actual 数值", async () => {
    const payload = {
      outcome_id: 31,
      evidence_field: "window_28d" as const,
      metric_path: "metrics.views_median",
      correlation_id: "prediction-actual-run-31-correlation",
      notes: "经理核验",
    };
    const receipt = await recordPredictionActual("tok", "run/with space", payload);
    expect(receipt.id).toBe(18);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/prediction-ledger/runs/run%2Fwith%20space/actual-from-outcome");
    expect(init).toMatchObject({ method: "POST", cache: "no-store" });
    const body = JSON.parse(init.body);
    expect(body).toEqual(payload);
    expect(body).not.toHaveProperty("actual_value");
    expect(body).not.toHaveProperty("prev_actual");
    expect(token).toBe("tok");
  });
});
