import { create } from "zustand";

import type { ViaActivityState } from "../components/catographer/viaActivity";
import type { SurfaceKey } from "../lib/contracts.generated";
import { isUploadErrorState, type ViaProgressSnapshot } from "../lib/submissionProgress";

export interface ViaMessage {
  id: string;
  role: "via" | "user";
  title?: string;
  text: string;
  quickActions?: string[];
  behaviorMode?: string;
  sceneHint?: string;
  provider?: string;
  activityState?: ViaActivityState | null;
}

interface ViaState {
  sessionKey: string;
  messages: ViaMessage[];
  isPanelOpen: boolean;
  isEmbeddedConversationOpen: boolean;
  progressSnapshot: ViaProgressSnapshot | null;
  dockOffset: { x: number; y: number };
  setSessionKey: (sessionKey: string) => void;
  appendMessage: (message: ViaMessage) => void;
  resetMessages: () => void;
  setPanelOpen: (isPanelOpen: boolean) => void;
  setEmbeddedConversationOpen: (isEmbeddedConversationOpen: boolean) => void;
  setProgressSnapshot: (snapshot: ViaProgressSnapshot | null | ((current: ViaProgressSnapshot | null) => ViaProgressSnapshot | null)) => void;
  setDockOffset: (dockOffset: { x: number; y: number }) => void;
  clearRuntimeState: () => void;
  togglePanel: () => void;
}

const PROGRESS_SNAPSHOT_STORAGE_KEY = "via-progress-snapshot";
const ACTIVE_PROGRESS_MAX_AGE_MS = 6 * 60 * 60 * 1000;
const INACTIVE_PROGRESS_MAX_AGE_MS = 10 * 60 * 1000;
const ERROR_PROGRESS_MAX_AGE_MS = 5 * 60 * 1000;

function readProgressSnapshot(): ViaProgressSnapshot | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<ViaProgressSnapshot> | null;
    if (!parsed?.surface || !parsed?.statusLine || typeof parsed?.step !== "number") {
      window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
      return null;
    }
    const hasMeaningfulProgress = Boolean(
      parsed.jobId
      || parsed.step > 1
      || isUploadErrorState(String(parsed.statusLine || "")),
    );
    if (!hasMeaningfulProgress) {
      window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
      return null;
    }
    const updatedAt = Number(parsed.updatedAt || Date.now());
    const isActive = Boolean(parsed.isActive && parsed.jobId);
    const ageMs = Date.now() - updatedAt;
    const maxAgeMs = isActive
      ? ACTIVE_PROGRESS_MAX_AGE_MS
      : isUploadErrorState(String(parsed.statusLine || ""))
        ? ERROR_PROGRESS_MAX_AGE_MS
        : INACTIVE_PROGRESS_MAX_AGE_MS;
    if (!Number.isFinite(updatedAt) || ageMs > maxAgeMs) {
      window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
      return null;
    }
    return {
      surface: parsed.surface as SurfaceKey,
      step: parsed.step,
      statusLine: parsed.statusLine,
      jobId: parsed.jobId,
      sourceLabel: parsed.sourceLabel,
      sourceKind: parsed.sourceKind,
      updatedAt,
      isActive,
    };
  } catch {
    window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
    return null;
  }
}

export const useViaStore = create<ViaState>((set) => ({
  sessionKey: "",
  messages: [],
  isPanelOpen: false,
  isEmbeddedConversationOpen: false,
  progressSnapshot: readProgressSnapshot(),
  dockOffset: (() => {
    if (typeof window === "undefined") {
      return { x: 0, y: 0 };
    }
    try {
      const raw = window.localStorage.getItem("via-dock-offset");
      if (!raw) {
        return { x: 0, y: 0 };
      }
      const parsed = JSON.parse(raw) as { x?: number; y?: number };
      return {
        x: Number(parsed?.x || 0),
        y: Number(parsed?.y || 0),
      };
    } catch {
      return { x: 0, y: 0 };
    }
  })(),
  setSessionKey: (sessionKey) => set({ sessionKey }),
  appendMessage: (message) =>
    set((state) => {
      const previous = state.messages[state.messages.length - 1];
      if (
        state.messages.some((item) => item.id === message.id) ||
        (previous &&
          previous.role === message.role &&
          previous.text === message.text &&
          (previous.title || "") === (message.title || ""))
      ) {
        return state;
      }
      return { messages: [...state.messages, message] };
    }),
  resetMessages: () => set({ messages: [] }),
  setPanelOpen: (isPanelOpen) => set({ isPanelOpen }),
  setEmbeddedConversationOpen: (isEmbeddedConversationOpen) => set({ isEmbeddedConversationOpen }),
  setProgressSnapshot: (snapshot) =>
    set((state) => {
      const progressSnapshot = typeof snapshot === "function" ? snapshot(state.progressSnapshot) : snapshot;
      if (typeof window !== "undefined") {
        if (progressSnapshot) {
          window.localStorage.setItem(PROGRESS_SNAPSHOT_STORAGE_KEY, JSON.stringify(progressSnapshot));
        } else {
          window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
        }
      }
      return { progressSnapshot };
    }),
  setDockOffset: (dockOffset) => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem("via-dock-offset", JSON.stringify(dockOffset));
    }
    set({ dockOffset });
  },
  clearRuntimeState: () => {
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(PROGRESS_SNAPSHOT_STORAGE_KEY);
    }
    set({
      messages: [],
      isPanelOpen: false,
      isEmbeddedConversationOpen: false,
      progressSnapshot: null,
    });
  },
  togglePanel: () => set((state) => ({ isPanelOpen: !state.isPanelOpen })),
}));
