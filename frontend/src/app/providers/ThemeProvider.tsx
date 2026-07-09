import {
  createContext,
  useCallback,
  useContext,
  useState,
  type PropsWithChildren,
} from "react";

export type Theme = "light" | "dark";
export type Style = "glass" | "instrument" | "commandos";

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
  setTheme: (t: Theme) => void;
  setStyle: (s: Style) => void;
  toggleTheme: () => void;
  cycleStyle: () => void;
};

const Ctx = createContext<ThemeCtx | null>(null);

function readAttr(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  return document.documentElement.getAttribute(name) || fallback;
}
function persist(theme: Theme, style: Style): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ theme, style }));
  } catch {
    /* localStorage 不可用时忽略 */
  }
}

export function ThemeProvider({ children }: PropsWithChildren) {
  // 初值直接读 index.html 首帧脚本已写好的属性,避免 hydration 抖动
  const [theme, setThemeState] = useState<Theme>(() => readAttr("data-theme", "light") as Theme);
  const [style, setStyleState] = useState<Style>(() => readAttr("data-style", "glass") as Style);

  const setTheme = useCallback((t: Theme) => {
    document.documentElement.setAttribute("data-theme", t);
    setThemeState(t);
    persist(t, readAttr("data-style", "glass") as Style);
  }, []);

  const setStyle = useCallback((s: Style) => {
    document.documentElement.setAttribute("data-style", s);
    setStyleState(s);
    persist(readAttr("data-theme", "light") as Theme, s);
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
      value={{ theme, style, styleLabel: STYLE_LABEL[style], setTheme, setStyle, toggleTheme, cycleStyle }}
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
