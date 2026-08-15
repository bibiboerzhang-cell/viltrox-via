import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
const jsonBody = (payload: unknown) => JSON.stringify(payload);

vi.mock("../http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => jsonBody(payload),
}));

import { revealKolPoolContact } from "./kolPool-api.detail";

beforeEach(() => {
  apiFetch.mockReset().mockResolvedValue({});
});

describe("revealKolPoolContact", () => {
  it("uses the audited single-KOL endpoint for an automatic drawer view", async () => {
    const controller = new AbortController();
    await revealKolPoolContact("token", 42, { signal: controller.signal });

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path, init, token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-pool/42/contacts/reveal");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ confirm: true, purpose: "kol_detail_view" }),
      cache: "no-store",
      signal: controller.signal,
    });
    expect(JSON.parse(init.body)).toEqual({ confirm: true, purpose: "kol_detail_view" });
    expect(token).toBe("token");
  });

  it("keeps compose_outreach for a standalone invite compatibility flow", async () => {
    await revealKolPoolContact("token", 43, { purpose: "compose_outreach" });

    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/kol-pool/43/contacts/reveal");
    expect(JSON.parse(init.body)).toEqual({ confirm: true, purpose: "compose_outreach" });
  });
});
