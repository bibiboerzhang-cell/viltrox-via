import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getKolPoolWorkspace, listKolPool } from "../../../domains/kol";
import {
  aiTodayAttemptFingerprint,
  aiTodaySnapshotFailureReason,
  aiTodaySnapshotFingerprint,
  fetchAiTodayHotSnapshot,
  fetchCockpitDashboardBundle,
  fetchCockpitShellBundle,
  isAiTodaySnapshotReady,
  runAiTodaySchedulerNow,
} from "./api";
import { toCockpitKolPoolRows } from "./kolPoolRuntime";
import { readCachedResource, writeCachedResource } from "./lib/resourceCache";
import { normalizeAlerts, normalizeCurrentUser, normalizeCockpitDashboard } from "./normalizers";

// Bump this key whenever the normalized KOL row shape changes. Reusing the
// previous key can briefly render stale rows (for example, missing fit scores)
// after a release even though the database and API already contain the data.
const KOL_POOL_CACHE_KEY = "cockpit.kol_pool.rows.v3";
const DASHBOARD_CACHE_KEY = "cockpit.dashboard.bundle.v1";
const KOL_POOL_REFRESH_MS = 10 * 60 * 1000;
const DASHBOARD_REFRESH_MS = 90 * 1000;
const AI_TODAY_POLL_ATTEMPTS = 40;
const AI_TODAY_POLL_INTERVAL_MS = 1500;

export type AiTodayRegenerationState = {
  phase: "idle" | "running" | "success" | "degraded" | "error";
  message: string;
};

const IDLE_AI_TODAY_REGENERATION: AiTodayRegenerationState = {
  phase: "idle",
  message: "",
};

function scopedCacheKey(base: string, token: string) {
  // Persistent cache must never cross account/session boundaries. Keep the token
  // itself out of IndexedDB while still producing a stable namespace per login.
  let hash = 2166136261;
  for (let index = 0; index < token.length; index += 1) {
    hash ^= token.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${base}.${(hash >>> 0).toString(36)}`;
}

function hasDashboardSummary(bundle: any) {
  return bundle?._sources?.dashboard?.ok === true
    && bundle?.dashboard?.summary
    && typeof bundle.dashboard.summary === "object";
}

function hasSnapshotValue(value: unknown) {
  return Boolean(value && typeof value === "object" && Object.keys(value as object).length);
}

function retainLastReadyAiToday(current: any, incoming: any) {
  if (isAiTodaySnapshotReady(incoming?.aiTodayHot)) return incoming;
  if (!isAiTodaySnapshotReady(current?.aiTodayHot)) return incoming;
  const incomingAttempt = incoming?.aiTodayHot?.latest_attempt
    || incoming?.aiTodayHot?.content?.latest_attempt;
  if (!incomingAttempt || typeof incomingAttempt !== "object") {
    return { ...incoming, aiTodayHot: current.aiTodayHot };
  }
  const retainedContent = current.aiTodayHot?.content && typeof current.aiTodayHot.content === "object"
    ? { ...current.aiTodayHot.content, latest_attempt: incomingAttempt }
    : current.aiTodayHot?.content;
  return {
    ...incoming,
    aiTodayHot: {
      ...current.aiTodayHot,
      latest_attempt: incomingAttempt,
      ...(retainedContent ? { content: retainedContent } : {}),
    },
  };
}

function waitForRuntimePoll(delayMs: number) {
  return new Promise<void>((resolve) => {
    if (typeof window === "undefined") {
      setTimeout(resolve, delayMs);
      return;
    }
    window.setTimeout(resolve, delayMs);
  });
}

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
  const [aiTodayRegeneration, setAiTodayRegeneration] = useState<AiTodayRegenerationState>(IDLE_AI_TODAY_REGENERATION);
  const dashboardRawRef = useRef<any>(dashboardRaw);
  const mountedRef = useRef(true);
  const apiTokenRef = useRef(apiToken);
  const regenerationOperationRef = useRef(0);
  const regenerationInFlightRef = useRef(false);
  dashboardRawRef.current = dashboardRaw;
  apiTokenRef.current = apiToken;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    regenerationOperationRef.current += 1;
    regenerationInFlightRef.current = false;
    setAiTodayRegeneration(IDLE_AI_TODAY_REGENERATION);
  }, [apiToken]);

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
    const cacheKey = scopedCacheKey(KOL_POOL_CACHE_KEY, apiToken);
    setKolPoolLoading(true);
    setKolPoolError("");

    const refreshRows = () => loadKolPoolWorkspaceRows(apiToken)
      .catch(() => listAllKolPoolPages(apiToken))
      .then((response) => {
        if (!cancelled) {
          const rows = toCockpitKolPoolRows(response || []);
          setKolPoolRows(rows);
          void writeCachedResource(cacheKey, rows);
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

    readCachedResource(cacheKey)
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
    let intervalId = 0;
    const cacheKey = scopedCacheKey(DASHBOARD_CACHE_KEY, apiToken);
    setDashboardLoading(true);
    setDashboardError("");

    const refreshDashboard = (forceRefresh = true) => fetchCockpitDashboardBundle(apiToken, { forceRefresh })
      .then((bundle) => {
        if (!hasDashboardSummary(bundle)) {
          throw new Error("Dashboard 主数据源未返回 summary，已保留上一份有效数据");
        }
        if (!cancelled) {
          const next = retainLastReadyAiToday(dashboardRawRef.current, bundle);
          dashboardRawRef.current = next;
          setDashboardRaw(next);
          setDashboardError("");
          void writeCachedResource(cacheKey, next);
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

    const refreshWhenVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      void refreshDashboard(true);
    };

    readCachedResource(cacheKey)
      .then((cached: any) => {
        if (cancelled) return;
        hasCachedBundle = Boolean(cached?.value);
        if (hasCachedBundle) {
          const next = { ...(cached.value || {}), _cache_status: "stale-while-revalidate", _cache_saved_at: cached.savedAt };
          dashboardRawRef.current = next;
          setDashboardRaw(next);
          setDashboardLoading(false);
        }
        if (!hasCachedBundle) setDashboardLoading(true);
        cancelRefresh = scheduleRuntimeRefresh(() => refreshDashboard(true), hasCachedBundle ? 250 : 0);
      })
      .catch(() => {
        if (!cancelled) cancelRefresh = scheduleRuntimeRefresh(() => refreshDashboard(true), 0);
      });

    intervalId = window.setInterval(refreshWhenVisible, DASHBOARD_REFRESH_MS);
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);

    return () => {
      cancelled = true;
      cancelRefresh();
      window.clearInterval(intervalId);
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [apiToken]);

  const regenerateAiToday = useCallback(async () => {
    if (!apiToken) {
      setAiTodayRegeneration({ phase: "error", message: "未登录，无法触发 AI Today 任务。" });
      return false;
    }
    if (regenerationInFlightRef.current) return false;

    regenerationInFlightRef.current = true;
    const operationId = regenerationOperationRef.current + 1;
    regenerationOperationRef.current = operationId;
    const baselineBundle = dashboardRawRef.current || {};
    const baselineSnapshot = baselineBundle.aiTodayHot;
    const baselineFingerprint = aiTodaySnapshotFingerprint(baselineSnapshot);
    const baselineAttemptFingerprint = aiTodayAttemptFingerprint(baselineSnapshot);
    const retainedMessage = hasSnapshotValue(baselineSnapshot)
      ? "继续显示上一份快照"
      : "当前无可用快照";
    const isCurrent = () => mountedRef.current
      && apiTokenRef.current === apiToken
      && regenerationOperationRef.current === operationId;

    if (isCurrent()) {
      setAiTodayRegeneration({
        phase: "running",
        message: `已触发后台生成，${retainedMessage}。`,
      });
    }

    try {
      await runAiTodaySchedulerNow(apiToken);

      let polledSnapshot: any = null;
      let transientPollError = "";
      for (let attempt = 0; attempt < AI_TODAY_POLL_ATTEMPTS && isCurrent(); attempt += 1) {
        try {
          const candidate = await fetchAiTodayHotSnapshot(apiToken, { forceRefresh: true });
          const candidateFingerprint = aiTodaySnapshotFingerprint(candidate);
          const candidateAttemptFingerprint = aiTodayAttemptFingerprint(candidate);
          if (hasSnapshotValue(candidate) && (
            candidateFingerprint !== baselineFingerprint
            || candidateAttemptFingerprint !== baselineAttemptFingerprint
          )) {
            polledSnapshot = candidate;
            break;
          }
        } catch (error) {
          transientPollError = error instanceof Error ? error.message : "快照轮询失败";
        }
        if (attempt < AI_TODAY_POLL_ATTEMPTS - 1) {
          await waitForRuntimePoll(AI_TODAY_POLL_INTERVAL_MS);
        }
      }
      if (!isCurrent()) return false;

      // The point refresh is intentionally followed by a full forced bundle
      // refresh. This keeps the regenerated card and the rest of Cockpit on the
      // same source generation while still retaining the previous valid AI
      // snapshot when the new run is blocked or degraded.
      let refreshedBundle: any = null;
      try {
        refreshedBundle = await fetchCockpitDashboardBundle(apiToken, { forceRefresh: true });
      } catch {
        refreshedBundle = null;
      }
      if (!isCurrent()) return false;

      const refreshedSnapshot = refreshedBundle?.aiTodayHot;
      const refreshedChanged = hasSnapshotValue(refreshedSnapshot)
        && aiTodaySnapshotFingerprint(refreshedSnapshot) !== baselineFingerprint;
      const refreshedAttemptChanged = hasSnapshotValue(refreshedSnapshot)
        && aiTodayAttemptFingerprint(refreshedSnapshot) !== baselineAttemptFingerprint;
      const polledChanged = hasSnapshotValue(polledSnapshot)
        && aiTodaySnapshotFingerprint(polledSnapshot) !== baselineFingerprint;
      const readySnapshot = polledChanged && isAiTodaySnapshotReady(polledSnapshot)
        ? polledSnapshot
        : refreshedChanged && isAiTodaySnapshotReady(refreshedSnapshot)
          ? refreshedSnapshot
          : null;

      if (readySnapshot) {
        const nextBase = hasDashboardSummary(refreshedBundle) ? refreshedBundle : baselineBundle;
        const next = { ...nextBase, aiTodayHot: readySnapshot };
        dashboardRawRef.current = next;
        setDashboardRaw(next);
        setDashboardError("");
        void writeCachedResource(scopedCacheKey(DASHBOARD_CACHE_KEY, apiToken), next);
        setAiTodayRegeneration({ phase: "success", message: "AI Today 新快照已生成并刷新。" });
        return true;
      }

      const degradedSnapshot = polledSnapshot
        || (refreshedChanged || refreshedAttemptChanged ? refreshedSnapshot : null);
      if (degradedSnapshot) {
        const nextBase = hasDashboardSummary(refreshedBundle) ? refreshedBundle : baselineBundle;
        const next = retainLastReadyAiToday(
          baselineBundle,
          { ...nextBase, aiTodayHot: degradedSnapshot },
        );
        dashboardRawRef.current = next;
        setDashboardRaw(next);
        void writeCachedResource(scopedCacheKey(DASHBOARD_CACHE_KEY, apiToken), next);
        setAiTodayRegeneration({
          phase: "degraded",
          message: `新结果未通过就绪门禁（${aiTodaySnapshotFailureReason(degradedSnapshot)}）；${retainedMessage}。`,
        });
        return false;
      }

      setAiTodayRegeneration({
        phase: "degraded",
        message: `任务已触发，但新快照在 60 秒内未就绪${transientPollError ? `（${transientPollError}）` : ""}；${retainedMessage}。`,
      });
      return false;
    } catch (error) {
      if (isCurrent()) {
        const message = error instanceof Error ? error.message : "未知错误";
        setAiTodayRegeneration({
          phase: "error",
          message: `AI Today 重新生成失败（${message}）；${retainedMessage}。`,
        });
      }
      return false;
    } finally {
      if (regenerationOperationRef.current === operationId) {
        regenerationInFlightRef.current = false;
      }
    }
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
    aiTodayRegeneration,
    regenerateAiToday,
  };
}
