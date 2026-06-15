// Verbatim from vkpi_v6.15.7_integrated.html


import { STORAGE_KEY } from "../data/storageKey";

export function loadStoredState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (err) {
    console.warn("[V-KPI] localStorage load failed:", err);
    return {};
  }
}

export function saveStoredState(patch: any) {
  try {
    const current = loadStoredState();
    const next = { ...current, ...patch };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch (err) {
    console.warn("[V-KPI] localStorage save failed:", err);
  }
}
