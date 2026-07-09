import { Command, Gauge, Moon, Sparkles, Sun } from "lucide-react";

import { useTheme, type Style } from "../app/providers/ThemeProvider";
import "./ThemeSwitch.css";

const STYLE_ICON: Record<Style, typeof Sparkles> = {
  glass: Sparkles,
  instrument: Gauge,
  commandos: Command,
};

type Props = { compact?: boolean };

/** 明暗 + 风格(glass→instrument→commandos)切换。只吃 --ds-* token,零硬编码色。 */
export function ThemeSwitch({ compact = false }: Props) {
  const { theme, style, styleLabel, toggleTheme, cycleStyle } = useTheme();
  const StyleIcon = STYLE_ICON[style];
  return (
    <div className="ds-switch" role="group" aria-label="外观">
      <button
        type="button"
        className="ds-switch__btn"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "切换到浅色" : "切换到深色"}
        title={theme === "dark" ? "浅色" : "深色"}
      >
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </button>
      <button
        type="button"
        className="ds-switch__btn ds-switch__btn--style"
        onClick={cycleStyle}
        aria-label={`风格:${styleLabel},点击切换`}
        title={`风格:${styleLabel}`}
      >
        <StyleIcon size={15} />
        {!compact && <span className="ds-switch__label">{styleLabel}</span>}
      </button>
    </div>
  );
}
