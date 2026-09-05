import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { I18N_EN } from "./i18nEn";
import { I18N_EN_CAMPAIGN_PLAN } from "./i18nEnCampaignPlan";
import { makeT } from "../lib/i18n";

describe("campaign planning dictionary composition", () => {
  it("preserves every frozen English key/value and its original order at runtime", () => {
    const entries = Object.entries(I18N_EN);
    expect(entries).toHaveLength(1354);
    // Captured from the pre-split four-wave catalog, not from the new helper.
    expect(createHash("sha256").update(JSON.stringify(entries)).digest("hex"))
      .toBe("5afb770052419d94af75c32b404554ed06e039270c875007608270042b35d7e7");
  });

  it("retains the 117 domain entries at the original merge position and translates each", () => {
    const domain = Object.entries(I18N_EN_CAMPAIGN_PLAN);
    expect(domain).toHaveLength(117);
    expect(Object.entries(I18N_EN).slice(1237)).toEqual(domain);
    const translate = makeT("en", I18N_EN);
    for (const [key, value] of domain) expect(translate(key)).toBe(value);
    expect(I18N_EN["下一步"]).toBe("Next step"); // Earlier cross-domain entry remains in place.
  });
});
