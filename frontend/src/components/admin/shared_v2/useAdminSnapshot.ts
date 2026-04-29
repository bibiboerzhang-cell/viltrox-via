/**
 * useAdminSnapshot — shared fetcher for all admin tabs
 *
 * Each tab calls useAdminSnapshot(token, fetcher) and gets
 * { data, loading, error, refresh } back. Avoids per-tab boilerplate.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { AdminRequestIssue, AdminSnapshotPayload } from "../../../services/admin.service";

export interface SnapshotResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  issues: AdminRequestIssue[];
  refresh: () => void;
}

function isSnapshotEnvelope<T>(value: AdminSnapshotPayload<T>): value is { data: T; issues?: AdminRequestIssue[] } {
  return typeof value === "object" && value !== null && "data" in value;
}

export function useAdminSnapshot<T>(
  token: string,
  fetcher: (token: string) => Promise<AdminSnapshotPayload<T>>,
  deps: unknown[] = [],
): SnapshotResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<AdminRequestIssue[]>([]);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    setError(null);
    setIssues([]);
    fetcher(token)
      .then((res) => {
        if (!mountedRef.current) return;
        if (isSnapshotEnvelope(res)) {
          setData(res.data);
          setIssues(res.issues || []);
          return;
        }
        setData(res);
      })
      .catch((err) => {
        if (!mountedRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        setIssues([]);
      })
      .finally(() => {
        if (!mountedRef.current) return;
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, tick, ...deps]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, issues, refresh };
}
