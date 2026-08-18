import { describe, expect, it } from "vitest";

import { sessionDisplayState, smartKolSearchFingerprint } from "./SmartKolInputPanel.searchState";

describe("Smart KOL search display state", () => {
  it("keeps the displayed approval session after terminal polling stops", () => {
    expect(sessionDisplayState(81, false)).toEqual({ displayedSessionId: 81, pollingSessionId: 81 });
    expect(sessionDisplayState(81, true)).toEqual({ displayedSessionId: 81, pollingSessionId: null });
  });

  it("changes fingerprint for search filters but not for option ordering", () => {
    const base = {
      query: "  35mm   Portrait ", market: "us", platforms: ["youtube", "instagram"],
      languages: ["en", "ja"], profileTypes: ["reviewer"], excludeChinese: true, searchMode: "balanced",
    };
    expect(smartKolSearchFingerprint(base)).toBe(smartKolSearchFingerprint({
      ...base, query: "35MM portrait", platforms: ["instagram", "youtube"], languages: ["ja", "en"],
    }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, languages: ["en"] }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, market: "JP" }));
  });
});
