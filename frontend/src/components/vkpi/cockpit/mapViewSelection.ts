export const DASHBOARD_MAP_PRIORITY = ["kols", "dealers", "events"] as const;

export interface DashboardMapModeStatus {
  available?: boolean;
  loading?: boolean;
  error?: string;
}

export interface DashboardMapSelection {
  mode: string | null;
  pending: boolean;
  allUnavailable: boolean;
  error: string;
}

export function resolveDashboardMapSelection(
  selectedMode: string | null,
  viewModes: Record<string, DashboardMapModeStatus | undefined>,
): DashboardMapSelection {
  const selected = selectedMode ? viewModes[selectedMode] : undefined;

  // A valid explicit or stored choice wins over automatic priority.
  if (selectedMode && selected?.available) {
    return { mode: selectedMode, pending: false, allUnavailable: false, error: "" };
  }

  // Keep a selected source stable while it refreshes. Its validity is not known yet.
  if (selectedMode && selected?.loading) {
    return { mode: selectedMode, pending: true, allUnavailable: false, error: "" };
  }

  for (const mode of DASHBOARD_MAP_PRIORITY) {
    const status = viewModes[mode];
    if (status?.available) {
      return { mode, pending: false, allUnavailable: false, error: "" };
    }
    if (status?.loading) {
      return { mode: null, pending: true, allUnavailable: false, error: "" };
    }
  }

  const error = DASHBOARD_MAP_PRIORITY
    .map((mode) => viewModes[mode]?.error || "")
    .filter(Boolean)
    .join(" | ");

  return { mode: null, pending: false, allUnavailable: true, error };
}
