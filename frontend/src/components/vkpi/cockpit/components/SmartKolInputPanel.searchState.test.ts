import { describe, expect, it } from "vitest";

import {
  resumePausedSessionState,
  parseKolFollowerCount,
  sessionDisplayState,
  sessionPollStateAfterTimeout,
  smartKolSearchFingerprint,
} from "./SmartKolInputPanel.searchState";

describe("Smart KOL search display state", () => {
  it("keeps the displayed approval session after terminal polling stops", () => {
    expect(sessionDisplayState(81, false)).toEqual({ displayedSessionId: 81, pollingSessionId: 81 });
    expect(sessionDisplayState(81, true)).toEqual({ displayedSessionId: 81, pollingSessionId: null });
  });

  it("changes fingerprint for search filters but not for option ordering", () => {
    const base = {
      query: "  35mm   Portrait ", market: "us", platforms: ["youtube", "instagram"],
      languages: ["en", "ja"], profileTypes: ["reviewer"], excludeChinese: true, searchMode: "balanced",
      objective: "prospective_growth" as const,
      followersMin: "5万", followersMax: "1m", vertical: "portrait", gearContent: "yes",
    };
    expect(smartKolSearchFingerprint(base)).toBe(smartKolSearchFingerprint({
      ...base,
      query: "35MM portrait",
      platforms: ["instagram", "youtube"],
      languages: ["ja", "en"],
      followersMin: "50k",
      followersMax: "100万",
    }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, languages: ["en"] }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, market: "JP" }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, objective: "existing_evidence" }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, followersMin: "10万" }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, followersMax: "2m" }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, vertical: "food" }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, gearContent: "no" }));
    expect(smartKolSearchFingerprint({ ...base, followersMin: "0" })).toBe(
      smartKolSearchFingerprint({ ...base, followersMin: "" }),
    );
  });

  it("parses Chinese and English follower shorthand into one canonical count", () => {
    expect(parseKolFollowerCount("5万")).toEqual({ state: "valid", normalized: "50000", value: 50_000 });
    expect(parseKolFollowerCount("50K")).toEqual({ state: "valid", normalized: "50000", value: 50_000 });
    expect(parseKolFollowerCount("1m")).toEqual({ state: "valid", normalized: "1000000", value: 1_000_000 });
    expect(parseKolFollowerCount("1,500")).toEqual({ state: "valid", normalized: "1500", value: 1_500 });
    expect(parseKolFollowerCount("0")).toEqual({ state: "empty", normalized: "" });
    expect(parseKolFollowerCount("不限")).toEqual({ state: "invalid", normalized: "不限" });
  });

  it("pauses observation after timeout and resumes the same session without creating a new id", () => {
    expect(sessionPollStateAfterTimeout(1143, false)).toEqual({ pollingSessionId: null, pausedSessionId: 1143 });
    expect(sessionPollStateAfterTimeout(1142, true)).toEqual({ pollingSessionId: null, pausedSessionId: null });
    expect(resumePausedSessionState(1143)).toEqual({ pollingSessionId: 1143, pausedSessionId: null });
  });
});
