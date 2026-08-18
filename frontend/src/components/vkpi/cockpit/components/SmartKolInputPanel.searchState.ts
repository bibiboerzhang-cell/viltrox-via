export type SmartKolSearchFingerprintInput = {
  query: string;
  market: string;
  platforms: readonly string[];
  languages: readonly string[];
  profileTypes: readonly string[];
  excludeChinese: boolean;
  searchMode: string;
};

function normalizedList(values: readonly string[]): string[] {
  return Array.from(new Set(values.map((value) => String(value || "").trim().toLowerCase()).filter(Boolean))).sort();
}

export function smartKolSearchFingerprint(input: SmartKolSearchFingerprintInput): string {
  return JSON.stringify({
    query: String(input.query || "").trim().replace(/\s+/g, " ").toLowerCase(),
    market: String(input.market || "").trim().toUpperCase(),
    platforms: normalizedList(input.platforms),
    languages: normalizedList(input.languages),
    profileTypes: normalizedList(input.profileTypes),
    excludeChinese: input.excludeChinese === true,
    searchMode: String(input.searchMode || "balanced").trim().toLowerCase(),
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
