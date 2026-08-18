import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  createSearchRequestEpochGuard,
  useSessionScopedSelection,
} from "./SmartKolInputPanel.sessionEpoch";


describe("Smart KOL session epoch", () => {
  it("rejects a response from an older search request", () => {
    const guard = createSearchRequestEpochGuard();
    const first = guard.begin();
    const second = guard.begin();

    expect(guard.isCurrent(first)).toBe(false);
    expect(guard.isCurrent(second)).toBe(true);
  });

  it("keeps picks within one session and clears them on session change", () => {
    const { result, rerender } = renderHook(
      ({ sessionId }) => useSessionScopedSelection(sessionId),
      { initialProps: { sessionId: 51 as number | null } },
    );

    act(() => result.current.togglePick(11));
    expect([...result.current.pickedIds]).toEqual([11]);

    rerender({ sessionId: 51 });
    expect([...result.current.pickedIds]).toEqual([11]);

    rerender({ sessionId: 52 });
    expect([...result.current.pickedIds]).toEqual([]);
  });

  it("can clear picks when a new search starts before a session id exists", () => {
    const { result } = renderHook(() => useSessionScopedSelection(null));
    act(() => result.current.togglePick(11));
    act(() => result.current.clearPickedIds());
    expect([...result.current.pickedIds]).toEqual([]);
  });
});
