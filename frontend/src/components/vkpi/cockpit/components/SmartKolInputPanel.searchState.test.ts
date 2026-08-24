import { describe, expect, it } from "vitest";

import {
  resumePausedSessionState,
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
    };
    expect(smartKolSearchFingerprint(base)).toBe(smartKolSearchFingerprint({
      ...base, query: "35MM portrait", platforms: ["instagram", "youtube"], languages: ["ja", "en"],
    }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, languages: ["en"] }));
    expect(smartKolSearchFingerprint(base)).not.toBe(smartKolSearchFingerprint({ ...base, market: "JP" }));
  });

  it("pauses observation after timeout and resumes the same session without creating a new id", () => {
    expect(sessionPollStateAfterTimeout(1143, false)).toEqual({ pollingSessionId: null, pausedSessionId: 1143 });
    expect(sessionPollStateAfterTimeout(1142, true)).toEqual({ pollingSessionId: null, pausedSessionId: null });
    expect(resumePausedSessionState(1143)).toEqual({ pollingSessionId: 1143, pausedSessionId: null });
  });
});
