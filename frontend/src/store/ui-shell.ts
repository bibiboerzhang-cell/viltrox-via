import { create } from "zustand";

interface UiShellState {
  adminRailOpen: boolean;
  viaFloatingEnabled: boolean;
  setAdminRailOpen: (value: boolean) => void;
  setViaFloatingEnabled: (value: boolean) => void;
}

export const useUiShellStore = create<UiShellState>((set) => ({
  adminRailOpen: true,
  viaFloatingEnabled: true,
  setAdminRailOpen: (value) => set({ adminRailOpen: value }),
  setViaFloatingEnabled: (value) => set({ viaFloatingEnabled: value }),
}));
