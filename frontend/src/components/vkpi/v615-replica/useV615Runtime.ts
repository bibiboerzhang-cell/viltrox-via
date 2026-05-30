// @ts-nocheck
import { useEffect, useMemo, useState } from "react";
import { listKolPool } from "../../../domains/kol";
import { fetchV615DashboardBundle, fetchV615ShellBundle } from "./api";
import { toV615KolPoolRows } from "./kolPoolRuntime";
import { readCachedResource, writeCachedResource } from "./lib/resourceCache";
import { normalizeAlerts, normalizeCurrentUser, normalizeV615Dashboard } from "./normalizers";

const KOL_POOL_CACHE_KEY = "v615.kol_pool.rows.v1";
const DASHBOARD_CACHE_KEY = "v615.dashboard.bundle.v1";
const KOL_POOL_REFRESH_MS = 10 * 60 * 1000;
const DASHBOARD_REFRESH_MS = 90 * 1000;

function cacheAgeMs(savedAt) {
  const savedTime = typeof savedAt === "string" ? Date.parse(savedAt) : Number(savedAt || 0);
  return savedTime ? Date.now() - savedTime : Number.POSITIVE_INFINITY;
}

function scheduleRuntimeRefresh(work, delay = 0) {
  if (typeof window === "undefined") {
    void work();
    return () => {};
  }
  let timeoutId;
  let idleId;
  timeoutId = window.setTimeout(() => {
    if (typeof window.requestIdleCallback === "function") {
      idleId = window.requestIdleCallback(() => {
        void work();
      }, { timeout: 1800 });
      return;
    }
    void work();
  }, delay);
  return () => {
    if (timeoutId) window.clearTimeout(timeoutId);
    if (idleId && typeof window.cancelIdleCallback === "function") window.cancelIdleCallback(idleId);
  };
}

async function listAllKolPoolPages(apiToken) {
  const pageSize = 500;
  const maxRows = 2000;
  const pages = [];
  for (let offset = 0; offset < maxRows; offset += pageSize) {
    const response = await listKolPool(apiToken, { limit: pageSize, offset, refreshIfStale: false });
    const items = response.items || [];
    pages.push(...items);
    if (items.length < pageSize) break;
  }
  return pages;
}

export function useV615Runtime({ apiToken, userName, userRole, userAvatar }) {
  const [currentUser, setCurrentUser] = useState(() => normalizeCurrentUser(null, { userName, userRole, userAvatar }));
  const [runtimeNotifications, setRuntimeNotifications] = useState([]);
  const [runtimeReminders, setRuntimeReminders] = useState([]);
  const [kolPoolRows, setKolPoolRows] = useState([]);
  const [kolPoolLoading, setKolPoolLoading] = useState(false);
  const [kolPoolError, setKolPoolError] = useState("");
  const [dashboardRaw, setDashboardRaw] = useState({});
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState("");

  useEffect(() => {
    setCurrentUser(normalizeCurrentUser(null, { userName, userRole, userAvatar }));
  }, [userName, userRole, userAvatar]);

  useEffect(() => {
    if (!apiToken) {
      setRuntimeNotifications([]);
      setRuntimeReminders([]);
      return;
    }
    let cancelled = false;
    fetchV615ShellBundle(apiToken)
      .then((bundle) => {
        if (cancelled) return;
        if (bundle.user) setCurrentUser(normalizeCurrentUser(bundle.user, { userName, userRole, userAvatar }));
        const normalized = normalizeAlerts(bundle.alerts || []);
        setRuntimeNotifications(normalized.notifications);
        setRuntimeReminders(normalized.reminders);
      })
      .catch(() => {
        if (!cancelled) {
          setRuntimeNotifications([]);
          setRuntimeReminders([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, userName, userRole, userAvatar]);

  useEffect(() => {
    if (!apiToken) {
      setKolPoolRows([]);
      setKolPoolError("未登录 / 无 token");
      return;
    }
    let cancelled = false;
    let hasCachedValue = false;
    let cancelRefresh = () => {};
    setKolPoolLoading(true);
    setKolPoolError("");

    const refreshRows = () => listAllKolPoolPages(apiToken)
      .then((response) => {
        if (!cancelled) {
          const rows = toV615KolPoolRows(response || []);
          setKolPoolRows(rows);
          void writeCachedResource(KOL_POOL_CACHE_KEY, rows);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          if (!hasCachedValue) setKolPoolRows([]);
          setKolPoolError(error instanceof Error ? error.message : "KOL Pool API 加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setKolPoolLoading(false);
      });

    readCachedResource(KOL_POOL_CACHE_KEY)
      .then((cached) => {
        if (cancelled) return;
        hasCachedValue = Array.isArray(cached?.value);
        if (hasCachedValue) {
          setKolPoolRows(cached.value || []);
          setKolPoolLoading(false);
        }
        if (!hasCachedValue) setKolPoolLoading(true);
        cancelRefresh = scheduleRuntimeRefresh(refreshRows, hasCachedValue ? 350 : 0);
      })
      .catch(() => {
        if (!cancelled) cancelRefresh = scheduleRuntimeRefresh(refreshRows, 0);
      });

    return () => {
      cancelled = true;
      cancelRefresh();
    };
  }, [apiToken]);

  useEffect(() => {
    if (!apiToken) {
      setDashboardRaw({});
      setDashboardError("未登录 / 无 token");
      return;
    }
    let cancelled = false;
    let hasCachedBundle = false;
    let cancelRefresh = () => {};
    setDashboardLoading(true);
    setDashboardError("");

    const refreshDashboard = () => fetchV615DashboardBundle(apiToken)
      .then((bundle) => {
        if (!cancelled) {
          setDashboardRaw(bundle);
          void writeCachedResource(DASHBOARD_CACHE_KEY, bundle);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          if (!hasCachedBundle) setDashboardRaw({});
          setDashboardError(error instanceof Error ? error.message : "Dashboard API 加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setDashboardLoading(false);
      });

    readCachedResource(DASHBOARD_CACHE_KEY)
      .then((cached) => {
        if (cancelled) return;
        hasCachedBundle = Boolean(cached?.value);
        if (hasCachedBundle) {
          setDashboardRaw({ ...(cached.value || {}), _cache_status: "stale-while-revalidate", _cache_saved_at: cached.savedAt });
          setDashboardLoading(false);
        }
        if (!hasCachedBundle) setDashboardLoading(true);
        cancelRefresh = scheduleRuntimeRefresh(refreshDashboard, hasCachedBundle ? 250 : 0);
      })
      .catch(() => {
        if (!cancelled) cancelRefresh = scheduleRuntimeRefresh(refreshDashboard, 0);
      });

    return () => {
      cancelled = true;
      cancelRefresh();
    };
  }, [apiToken]);

  const dashboardRuntime = useMemo(() => normalizeV615Dashboard(dashboardRaw, kolPoolRows), [dashboardRaw, kolPoolRows]);

  return {
    currentUser,
    runtimeNotifications,
    setRuntimeNotifications,
    runtimeReminders,
    kolPoolRows,
    kolPoolLoading,
    kolPoolError,
    dashboardRuntime,
    dashboardLoading,
    dashboardError,
  };
}
