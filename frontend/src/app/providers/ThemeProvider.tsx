import {
  createContext,
  useCallback,
  useContext,
  useState,
  type PropsWithChildren,
} from "react";

export type Theme = "light" | "dark";
export type Style = "glass" | "instrument" | "commandos";

const DEFAULT_GLASS_OPACITY = 78;
const MIN_GLASS_OPACITY = 60;
const MAX_GLASS_OPACITY = 96;

const KEY = "vkpi-ui-pref-v1";
const STYLE_ORDER: Style[] = ["glass", "instrument", "commandos"];
const STYLE_LABEL: Record<Style, string> = {
  glass: "玻璃",
  instrument: "仪器",
  commandos: "单色",
};

type ThemeCtx = {
  theme: Theme;
  style: Style;
  styleLabel: string;
  glassOpacity: number;
  setTheme: (t: Theme) => void;
  setStyle: (s: Style) => void;
  setGlassOpacity: (value: number) => void;
  toggleTheme: () => void;
  cycleStyle: () => void;
};

const Ctx = createContext<ThemeCtx | null>(null);

function readAttr(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return document.documentElement.getAttribute(name) || fallback;
}

function normalizeGlassOpacity(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_GLASS_OPACITY;
  return Math.min(MAX_GLASS_OPACITY, Math.max(MIN_GLASS_OPACITY, Math.round(parsed)));
}

function applyGlassOpacity(value: number): void {
  if (typeof document === "undefined") return;
  const normalized = normalizeGlassOpacity(value);
  const root = document.documentElement;
  const opacity = normalized / 100;
  root.setAttribute("data-glass-opacity", String(normalized));
  root.style.setProperty("--vkpi-glass-opacity", String(opacity));
  root.style.setProperty("--vkpi-glass-card-opacity", String(Math.min(0.98, opacity + 0.08)));
  root.style.setProperty("--vkpi-glass-overlay-opacity", String(Math.max(0.9, opacity)));
  root.style.setProperty("--vkpi-glass-drawer-opacity", String(Math.max(0.88, opacity)));
}

function persist(theme: Theme, style: Style, glassOpacity: number): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ theme, style, glassOpacity }));
  } catch {
    /* localStorage 不可用时忽略 */
  }
}

export function ThemeProvider({ children }: PropsWithChildren) {
  // 初值直接读 index.html 首帧脚本已写好的属性,避免 hydration 抖动
  const [theme, setThemeState] = useState<Theme>(() => readAttr("data-theme", "light") as Theme);
  const [style, setStyleState] = useState<Style>(() => readAttr("data-style", "glass") as Style);
  const [glassOpacity, setGlassOpacityState] = useState(() => normalizeGlassOpacity(
    readAttr("data-glass-opacity", String(DEFAULT_GLASS_OPACITY)),
  ));

  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    setThemeState(t);
    persist(
      t,
      readAttr("data-style", "glass") as Style,
      normalizeGlassOpacity(readAttr("data-glass-opacity", String(DEFAULT_GLASS_OPACITY))),
    );
  }, []);

  const setStyle = useCallback((s: Style) => {
    document.documentElement.setAttribute("data-style", s);
    setStyleState(s);
    persist(
      readAttr("data-theme", "light") as Theme,
      s,
      normalizeGlassOpacity(readAttr("data-glass-opacity", String(DEFAULT_GLASS_OPACITY))),
    );
  }, []);

  const setGlassOpacity = useCallback((value: number) => {
    const normalized = normalizeGlassOpacity(value);
    applyGlassOpacity(normalized);
    setGlassOpacityState(normalized);
    persist(
      readAttr("data-theme", "light") as Theme,
      readAttr("data-style", "glass") as Style,
      normalized,
    );
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme(readAttr("data-theme", "light") === "dark" ? "light" : "dark");
  }, [setTheme]);

  const cycleStyle = useCallback(() => {
    const cur = readAttr("data-style", "glass") as Style;
    const i = STYLE_ORDER.indexOf(cur);
    setStyle(STYLE_ORDER[(i + 1) % STYLE_ORDER.length]);
  }, [setStyle]);

  return (
    <Ctx.Provider
      value={{
        theme,
        style,
        styleLabel: STYLE_LABEL[style],
        glassOpacity,
        setTheme,
        setStyle,
        setGlassOpacity,
        toggleTheme,
        cycleStyle,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useTheme(): ThemeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme must be used within ThemeProvider");
  return v;
}
