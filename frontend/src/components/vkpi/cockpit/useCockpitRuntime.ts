import { useEffect, useMemo, useState } from "react";
import { getKolPoolWorkspace, listKolPool } from "../../../domains/kol";
import { fetchCockpitDashboardBundle, fetchCockpitShellBundle } from "./api";
import { toCockpitKolPoolRows } from "./kolPoolRuntime";
import { readCachedResource, writeCachedResource } from "./lib/resourceCache";
import { normalizeAlerts, normalizeCurrentUser, normalizeCockpitDashboard } from "./normalizers";

const KOL_POOL_CACHE_KEY = "cockpit.kol_pool.rows.v1";
const DASHBOARD_CACHE_KEY = "cockpit.dashboard.bundle.v1";
const KOL_POOL_REFRESH_MS = 10 * 60 * 1000;
const DASHBOARD_REFRESH_MS = 90 * 1000;

function cacheAgeMs(savedAt: any) {
  const savedTime = typeof savedAt === "string" ? Date.parse(savedAt) : Number(savedAt || 0);
  return savedTime ? Date.now() - savedTime : Number.POSITIVE_INFINITY;
}

function scheduleRuntimeRefresh(work: any, delay = 0) {
  if (typeof window === "undefined") {
    void work();
    return () => {};
  }
  let timeoutId: any;
  let idleId: any;
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

async function listAllKolPoolPages(apiToken: any) {
  const pageSize = 500;
  const maxRows = 2000;
  const pages: any[] = [];
  for (let offset = 0; offset < maxRows; offset += pageSize) {
    const response = await listKolPool(apiToken, { limit: pageSize, offset, refreshIfStale: false });
    const items = response.items || [];
    pages.push(...items);
    if (items.length < pageSize) break;
  }
  return pages;
}

async function loadKolPoolWorkspaceRows(apiToken: any) {
  // 分页拉全量:此前固定 limit=1200 < 池实际行数(1353)→ 尾部约 150 条历史 KOL 永不显示
  // (用户「过往搜索的人没加进来」的真因之一)。逐页拉到取尽为止,硬上限 8000 防跑飞。
  const pageSize = 1000;
  const hardCap = 8000;
  const rows: any[] = [];
  for (let offset = 0; offset < hardCap; offset += pageSize) {
    const response = await getKolPoolWorkspace(apiToken, { limit: pageSize, offset, sortBy: "fit" });
    const items = response?.list?.items || [];
    if (!Array.isArray(items) || items.length === 0) break;
    rows.push(...items);
    if (items.length < pageSize) break;
  }
  return rows;
}

export function useCockpitRuntime({ apiToken, userName, userRole, userAvatar, userEmail = "", userAuthRole = "", starredProjects }: any) {
  const [currentUser, setCurrentUser] = useState(() => normalizeCurrentUser(null, { userName, userRole: userAuthRole || userRole, userAvatar, userEmail }));
  const [runtimeNotifications, setRuntimeNotifications] = useState<any[]>([]);
  const [runtimeReminders, setRuntimeReminders] = useState<any[]>([]);
  const [kolPoolRows, setKolPoolRows] = useState<any[]>([]);
  const [kolPoolLoading, setKolPoolLoading] = useState(false);
  const [kolPoolError, setKolPoolError] = useState("");
  const [dashboardRaw, setDashboardRaw] = useState<any>({});
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardError, setDashboardError] = useState("");

  useEffect(() => {
    setCurrentUser(normalizeCurrentUser(null, { userName, userRole: userAuthRole || userRole, userAvatar, userEmail }));
  }, [userName, userRole, userAvatar, userEmail, userAuthRole]);

  useEffect(() => {
    if (!apiToken) {
      setRuntimeNotifications([]);
      setRuntimeReminders([]);
      return;
    }
    let cancelled = false;
    fetchCockpitShellBundle(apiToken)
      .then((bundle) => {
        if (cancelled) return;
        if (bundle.user) setCurrentUser(normalizeCurrentUser(bundle.user, { userName, userRole: userAuthRole || userRole, userAvatar, userEmail }));
        const normalized = normalizeAlerts((bundle.alerts || []) as any);
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
  }, [apiToken, userName, userRole, userAvatar, userEmail, userAuthRole]);

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

    const refreshRows = () => loadKolPoolWorkspaceRows(apiToken)
      .catch(() => listAllKolPoolPages(apiToken))
      .then((response) => {
        if (!cancelled) {
          const rows = toCockpitKolPoolRows(response || []);
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
      .then((cached: any) => {
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

    const refreshDashboard = () => fetchCockpitDashboardBundle(apiToken)
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
      .then((cached: any) => {
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

  const dashboardRuntime = useMemo(() => normalizeCockpitDashboard({
    ...dashboardRaw,
    starredProjects: Array.isArray(starredProjects) ? starredProjects : dashboardRaw.starredProjects,
  }, kolPoolRows), [dashboardRaw, kolPoolRows, starredProjects]);

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
