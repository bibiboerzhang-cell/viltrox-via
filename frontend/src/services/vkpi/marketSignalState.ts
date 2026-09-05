/** Read coverage, not market confidence. Mirrors summary_signal_state.py. */
export const MARKET_SIGNAL_SOURCES = ["brand_pulse", "category_tracks", "market_voice"] as const;
export type SignalSources = Partial<Record<typeof MARKET_SIGNAL_SOURCES[number], { status: string }>>;
type SignalSection = { status: string; items: unknown[]; sources?: SignalSources };
const COMPLETE = new Set(["ok", "ready", "empty", "no_data_in_window", "no_brand_signal"]);
const UNAVAILABLE = new Set(["error", "unavailable", "scope_unavailable", "failed"]);

export function marketSignalReadState(section: SignalSection): "ok" | "empty" | "partial" | "error" | "unknown" {
  const declared = section.status.trim().toLowerCase();
  if (UNAVAILABLE.has(declared)) return "error";
  if (section.sources && Object.keys(section.sources).length > 0) {
    const complete = MARKET_SIGNAL_SOURCES.filter((name) =>
      COMPLETE.has((section.sources?.[name]?.status || "").trim().toLowerCase()),
    ).length;
    // Old servers may say empty/ok despite one failed internal source.
    if (complete < MARKET_SIGNAL_SOURCES.length) {
      return complete > 0 || section.items.length > 0 ? "partial" : "error";
    }
  }
  if (["partial", "degraded", "stale"].includes(declared)) return "partial";
  if (["ok", "ready", "empty"].includes(declared)) {
    if (declared === "empty" && section.items.length > 0) return "partial";
    return section.items.length > 0 ? "ok" : "empty";
  }
  return "unknown";
}

export function completeSignalCount(section: SignalSection): number | null {
  const state = marketSignalReadState(section);
  return state === "ok" || state === "empty" ? section.items.length : null;
}
