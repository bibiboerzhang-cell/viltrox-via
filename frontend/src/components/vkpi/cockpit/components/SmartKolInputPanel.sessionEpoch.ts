import { useCallback, useEffect, useRef, useState } from "react";

export type SearchRequestEpoch = number;

export function createSearchRequestEpochGuard(initialEpoch = 0) {
  let current = Math.max(0, Math.trunc(initialEpoch));
  return {
    begin(): SearchRequestEpoch {
      current += 1;
      return current;
    },
    isCurrent(epoch: SearchRequestEpoch): boolean {
      return epoch === current;
    },
    current(): SearchRequestEpoch {
      return current;
    },
  };
}

export function useLatestSearchRequestEpoch() {
  const guardRef = useRef<ReturnType<typeof createSearchRequestEpochGuard>>();
  if (!guardRef.current) guardRef.current = createSearchRequestEpochGuard();
  const beginSearchRequest = useCallback(() => guardRef.current!.begin(), []);
  const isCurrentSearchRequest = useCallback(
    (epoch: SearchRequestEpoch) => guardRef.current!.isCurrent(epoch),
    [],
  );
  const currentSearchRequest = useCallback(() => guardRef.current!.current(), []);
  return { beginSearchRequest, currentSearchRequest, isCurrentSearchRequest };
}

export function useSessionScopedSelection(sessionId: number | null) {
  const [pickedIds, setPickedIds] = useState<Set<number>>(() => new Set());
  const previousSessionId = useRef<number | null>(sessionId);

  useEffect(() => {
    if (previousSessionId.current === sessionId) return;
    previousSessionId.current = sessionId;
    setPickedIds(new Set());
  }, [sessionId]);

  const clearPickedIds = useCallback(() => setPickedIds(new Set()), []);
  const togglePick = useCallback((id: number) => {
    setPickedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return { pickedIds, setPickedIds, clearPickedIds, togglePick };
}
