export type SmartKolSearchFingerprintInput = {
  query: string;
  objective: "prospective_growth" | "existing_evidence";
  market: string;
  platforms: readonly string[];
  languages: readonly string[];
  profileTypes: readonly string[];
  excludeChinese: boolean;
  searchMode: string;
  followersMin?: string;
  followersMax?: string;
  vertical?: string;
  gearContent?: string;
};

export type KolFollowerCountParseResult =
  | { state: "empty"; normalized: "" }
  | { state: "valid"; normalized: string; value: number }
  | { state: "invalid"; normalized: string };

/**
 * Parse the follower shorthand operators use in Chinese and English search briefs.
 * Blank and zero both mean "not constrained"; this keeps `0` from looking like an
 * active zero-follower audience segment while the API interprets it as a floor.
 */
export function parseKolFollowerCount(raw: string): KolFollowerCountParseResult {
  const normalizedRaw = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[,_，]/g, "")
    .replace(/\s+/g, "");
  if (!normalizedRaw) return { state: "empty", normalized: "" };

  const match = normalizedRaw.match(/^(\d+(?:\.\d+)?)(k|w|m|千|万|百万)?$/i);
  if (!match) return { state: "invalid", normalized: normalizedRaw };

  const base = Number(match[1]);
  const suffix = String(match[2] || "").toLowerCase();
  const multiplier = suffix === "k" || suffix === "千"
    ? 1_000
    : suffix === "w" || suffix === "万"
      ? 10_000
      : suffix === "m" || suffix === "百万"
        ? 1_000_000
        : 1;
  const value = Math.floor(base * multiplier);
  if (!Number.isSafeInteger(value) || value < 0) {
    return { state: "invalid", normalized: normalizedRaw };
  }
  if (value === 0) return { state: "empty", normalized: "" };
  return { state: "valid", normalized: String(value), value };
}

function normalizedFollowerFingerprint(raw: string | undefined): string {
  const parsed = parseKolFollowerCount(raw || "");
  // Invalid text must still invalidate a previous result. Prefix it so it can
  // never collide with a valid canonical integer.
  return parsed.state === "invalid" ? `invalid:${parsed.normalized}` : parsed.normalized;
}

function normalizedList(values: readonly string[]): string[] {
  return Array.from(new Set(values.map((value) => String(value || "").trim().toLowerCase()).filter(Boolean))).sort();
}

export function smartKolSearchFingerprint(input: SmartKolSearchFingerprintInput): string {
  return JSON.stringify({
    query: String(input.query || "").trim().replace(/\s+/g, " ").toLowerCase(),
    objective: input.objective === "existing_evidence" ? "existing_evidence" : "prospective_growth",
    market: String(input.market || "").trim().toUpperCase(),
    platforms: normalizedList(input.platforms),
    languages: normalizedList(input.languages),
    profileTypes: normalizedList(input.profileTypes),
    excludeChinese: input.excludeChinese === true,
    searchMode: String(input.searchMode || "balanced").trim().toLowerCase(),
    followersMin: normalizedFollowerFingerprint(input.followersMin),
    followersMax: normalizedFollowerFingerprint(input.followersMax),
    vertical: String(input.vertical || "").trim().toLowerCase(),
    gearContent: String(input.gearContent || "any").trim().toLowerCase(),
  });
}

export function sessionDisplayState(
  sessionId: number | null | undefined,
  terminal: boolean,
): { displayedSessionId: number | null; pollingSessionId: number | null } {
  const normalized = Number(sessionId);
  const displayedSessionId = Number.isFinite(normalized) && normalized > 0 ? normalized : null;
  return {
    displayedSessionId,
    pollingSessionId: displayedSessionId && !terminal ? displayedSessionId : null,
  };
}

export function sessionPollStateAfterTimeout(
  sessionId: number | null | undefined,
  terminal: boolean,
): { pollingSessionId: null; pausedSessionId: number | null } {
  const normalized = Number(sessionId);
  const validSessionId = Number.isFinite(normalized) && normalized > 0 ? normalized : null;
  return {
    pollingSessionId: null,
    pausedSessionId: terminal ? null : validSessionId,
  };
}

export function resumePausedSessionState(
  pausedSessionId: number | null | undefined,
): { pollingSessionId: number | null; pausedSessionId: null } {
  const normalized = Number(pausedSessionId);
  return {
    pollingSessionId: Number.isFinite(normalized) && normalized > 0 ? normalized : null,
    pausedSessionId: null,
  };
}
