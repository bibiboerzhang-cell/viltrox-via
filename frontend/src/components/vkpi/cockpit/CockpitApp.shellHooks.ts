import { useEffect, useState } from "react";

import { buildApiUrl } from "../../../services/http";

type NavigationSetters = {
  setActiveNav: (value: string) => void;
  setOpenLegacyProjectId: (value: string) => void;
};

export function useCockpitVersionBadge() {
  const [versionBadge, setVersionBadge] = useState<any>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch(buildApiUrl("/health"), {
          credentials: "same-origin",
        });
        if (!response.ok) return;
        const payload = await response.json();
        const build = (payload && payload.build) || {};
        if (!cancelled) {
          setVersionBadge({
            shortSha: String(build.git_short_sha || "").slice(0, 8),
            inSync: Boolean(build.client_matches_server),
            hasClient: Boolean(build.client_build),
          });
        }
      } catch {
        // Health is diagnostic only; an unavailable backend must not break the shell.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return versionBadge;
}

export function useCockpitPresenceHeartbeat(apiToken: string | undefined) {
  useEffect(() => {
    if (!apiToken) return;
    let stopped = false;
    const beat = () => {
      void fetch(buildApiUrl("/api/auth/me"), {
        credentials: "same-origin",
        headers: { Authorization: `Bearer ${apiToken}` },
      }).catch(() => undefined);
    };
    beat();
    const intervalId = window.setInterval(() => {
      if (!stopped) beat();
    }, 60_000);
    return () => {
      stopped = true;
      window.clearInterval(intervalId);
    };
  }, [apiToken]);
}

export function useCockpitNavigationEvents({
  setActiveNav,
  setOpenLegacyProjectId,
}: NavigationSetters) {
  useEffect(() => {
    const openKolPool = () => setActiveNav("kol-pool");
    const openMyKol = () => setActiveNav("my-kol");
    const openKolProfile = () => setActiveNav("kolProfile");
    const openProject = (event: Event) => {
      const projectId = String((event as CustomEvent)?.detail?.projectId || "");
      if (projectId) setOpenLegacyProjectId(projectId);
      setActiveNav("projects");
    };
    const listeners: Array<[string, EventListener]> = [
      ["vkpi:open-kol-profile", openKolProfile],
      ["vkpi:open-kol-search-session", openKolPool],
      ["vkpi:open-mykol-kol", openMyKol],
      ["vkpi:open-project-task", openProject as EventListener],
      ["vkpi:open-kol-pool-item", openKolPool],
      ["vkpi:open-kol-pool-search", openKolPool],
    ];
    listeners.forEach(([name, listener]) => window.addEventListener(name, listener));
    return () => {
      listeners.forEach(([name, listener]) =>
        window.removeEventListener(name, listener),
      );
    };
  }, [setActiveNav, setOpenLegacyProjectId]);
}
