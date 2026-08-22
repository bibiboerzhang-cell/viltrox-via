import { Command, Gauge, Moon, Sparkles, Sun } from "lucide-react";

import { useLocale } from "../app/providers/LocaleProvider";
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
  const { lang, languageLoading, t, toggleLang } = useLocale();
  const StyleIcon = STYLE_ICON[style];
  const localizedStyleLabel = t(styleLabel);
  const languageAction = lang === "zh" ? t("切换到英文") : t("切换到中文");
  const languageTarget = lang === "zh" ? "English" : "中文";
  return (
    <div className="ds-switch" role="group" aria-label={t("外观与语言")}>
      <button
        type="button"
        className="ds-switch__btn"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? t("切换到浅色") : t("切换到深色")}
        title={theme === "dark" ? t("浅色") : t("深色")}
      >
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </button>
      <button
        type="button"
        className="ds-switch__btn ds-switch__btn--style"
        onClick={cycleStyle}
        aria-label={t("风格:{style},点击切换", { style: localizedStyleLabel })}
        title={t("风格:{style}", { style: localizedStyleLabel })}
      >
        <StyleIcon size={15} />
        {!compact && <span className="ds-switch__label">{localizedStyleLabel}</span>}
      </button>
      <button
        type="button"
        className="ds-switch__btn ds-switch__btn--style"
        onClick={toggleLang}
        aria-label={languageAction}
        aria-busy={languageLoading}
        title={languageAction}
      >
        <span className="ds-switch__label">
          {languageLoading ? "…" : compact ? (lang === "zh" ? "EN" : "中") : languageTarget}
        </span>
      </button>
    </div>
  );
}
