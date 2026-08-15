import * as React from "react";

import { revealKolPoolContact } from "../../../../services/vkpi/kolPool-api";
import {
  contactErrorState,
  contactStateFromReveal,
  type ContactPurpose,
  type ContactState,
} from "./kolContacts";

type UseKolContactStateOptions = {
  apiToken: string;
  kolPoolId: string | number | null | undefined;
  purpose?: ContactPurpose;
  initialState?: ContactState | null;
};

type StateEnvelope = {
  identity: string;
  value: ContactState;
};

type ActiveRequest = {
  identity: string;
  retry: number;
  controller: AbortController;
  abortTimer: ReturnType<typeof setTimeout> | null;
};

const LOADING_STATE: ContactState = { status: "loading", contacts: [] };

/**
 * Ephemeral audited contact read for one mounted surface.
 *
 * The hook intentionally has no module cache. A response can only live in the
 * mounted component state; changing token/KOL identity returns `loading`
 * immediately, aborts the old request, and prevents a late response from
 * crossing the identity boundary.
 */
export function useKolContactState({
  apiToken,
  kolPoolId,
  purpose = "kol_detail_view",
  initialState = null,
}: UseKolContactStateOptions) {
  const tokenGenerationRef = React.useRef({ token: apiToken, generation: 0 });
  if (tokenGenerationRef.current.token !== apiToken) {
    tokenGenerationRef.current = {
      token: apiToken,
      generation: tokenGenerationRef.current.generation + 1,
    };
  }
  const normalizedId = String(kolPoolId ?? "").trim();
  const identity = `${tokenGenerationRef.current.generation}:${normalizedId}:${purpose}`;
  const reusableInitialState = initialState
    && ["full", "restricted", "empty"].includes(initialState.status)
    && initialState.auditedPurpose === purpose
    && initialState.auditedKolPoolId === normalizedId
    ? initialState
    : null;
  const [retryGeneration, setRetryGeneration] = React.useState(0);
  const [envelope, setEnvelope] = React.useState<StateEnvelope>(() => ({
    identity,
    value: reusableInitialState || LOADING_STATE,
  }));
  const activeRequestRef = React.useRef<ActiveRequest | null>(null);

  React.useEffect(() => {
    const abortActiveRequest = () => {
      const active = activeRequestRef.current;
      if (!active) return;
      if (active.abortTimer) clearTimeout(active.abortTimer);
      active.controller.abort();
      activeRequestRef.current = null;
    };
    const scheduleAbort = (active: ActiveRequest) => {
      // StrictMode replays an effect setup immediately after cleanup. Defer the
      // abort one task so the replay can adopt the same in-flight request.
      active.abortTimer = setTimeout(() => {
        if (activeRequestRef.current !== active) return;
        active.controller.abort();
        activeRequestRef.current = null;
      }, 0);
    };

    if (reusableInitialState && retryGeneration === 0) {
      abortActiveRequest();
      setEnvelope({ identity, value: reusableInitialState });
      return undefined;
    }
    if (!normalizedId) {
      abortActiveRequest();
      setEnvelope({ identity, value: { status: "empty", contacts: [], reason: "missing_kol_pool_id" } });
      return undefined;
    }
    if (!apiToken) {
      abortActiveRequest();
      setEnvelope({
        identity,
        value: { status: "error", contacts: [], reason: "missing_session", message: "登录状态无效，无法读取完整联系方式" },
      });
      return undefined;
    }

    const existing = activeRequestRef.current;
    if (existing?.identity === identity && existing.retry === retryGeneration) {
      if (existing.abortTimer) clearTimeout(existing.abortTimer);
      existing.abortTimer = null;
      return () => scheduleAbort(existing);
    }
    abortActiveRequest();
    const active: ActiveRequest = {
      identity,
      retry: retryGeneration,
      controller: new AbortController(),
      abortTimer: null,
    };
    activeRequestRef.current = active;
    setEnvelope({ identity, value: LOADING_STATE });
    void revealKolPoolContact(apiToken, kolPoolId as string | number, { signal: active.controller.signal, purpose })
      .then((payload) => {
        if (activeRequestRef.current !== active || active.controller.signal.aborted) return;
        setEnvelope({
          identity,
          value: {
            ...contactStateFromReveal(payload),
            auditedPurpose: purpose,
            auditedKolPoolId: normalizedId,
          },
        });
      })
      .catch((error: unknown) => {
        if (activeRequestRef.current !== active || active.controller.signal.aborted) return;
        setEnvelope({ identity, value: contactErrorState(error) });
      });

    return () => scheduleAbort(active);
  }, [apiToken, identity, normalizedId, purpose, retryGeneration, reusableInitialState]);

  const retry = React.useCallback(() => {
    setRetryGeneration((value) => value + 1);
  }, []);

  const clear = React.useCallback(() => {
    const active = activeRequestRef.current;
    if (active?.abortTimer) clearTimeout(active.abortTimer);
    active?.controller.abort();
    activeRequestRef.current = null;
    setEnvelope({
      identity,
      value: { status: "restricted", contacts: [], reason: "contact_state_cleared" },
    });
  }, [identity]);

  // Do not render the previous identity's plaintext during the render before
  // the new effect runs.
  const state = envelope.identity === identity ? envelope.value : LOADING_STATE;
  return { state, retry, clear };
}

export function useKolDrawerContactState(apiToken: string, kolPoolId: string | number | null | undefined) {
  return useKolContactState({ apiToken, kolPoolId, purpose: "kol_detail_view" });
}
