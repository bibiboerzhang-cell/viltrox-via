import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";

import { I18N_ZH } from "../../components/vkpi/cockpit/data/i18nZh";
import { ZH_SOFTEN } from "../../components/vkpi/cockpit/data/zhSoften";

export type AppLanguage = "zh" | "en";
export type TranslationValues = Record<string, string | number>;
export type Translate = (source: string, values?: TranslationValues) => string;

type LanguageSetter = (
  next: AppLanguage | ((current: AppLanguage) => AppLanguage),
) => void;

type I18nContextValue = {
  lang: AppLanguage;
  setLang: LanguageSetter;
  t: Translate;
  languageLoading?: boolean;
  toggleLang?: () => void;
};

type TranslationMap = Readonly<Record<string, string>>;

export const LOCALE_STORAGE_KEY = "vkpi-locale-v1";
const LEGACY_DASHBOARD_STORAGE_KEY = "vkpi-dashboard-state-v1";

let englishCatalogPromise: Promise<TranslationMap> | null = null;

function normalizeLanguage(value: unknown): AppLanguage | null {
  return value === "zh" || value === "en" ? value : null;
}

function parseStoredLanguage(raw: string | null): AppLanguage | null {
  if (!raw) return null;
  const direct = normalizeLanguage(raw);
  if (direct) return direct;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed === "string") return normalizeLanguage(parsed);
    if (parsed && typeof parsed === "object") {
      return normalizeLanguage((parsed as { lang?: unknown }).lang);
    }
  } catch {
    return null;
  }
  return null;
}

function readInitialLanguage(): AppLanguage {
  if (typeof window === "undefined") return "zh";
  try {
    const stored = parseStoredLanguage(window.localStorage.getItem(LOCALE_STORAGE_KEY));
    if (stored) return stored;

    // One-way compatibility bridge for the language preference that previously
    // lived inside Cockpit state. The legacy record remains untouched.
    const legacyRaw = window.localStorage.getItem(LEGACY_DASHBOARD_STORAGE_KEY);
    if (!legacyRaw) return "zh";
    const legacy = JSON.parse(legacyRaw) as { lang?: unknown };
    return normalizeLanguage(legacy?.lang) ?? "zh";
  } catch {
    return "zh";
  }
}

function persistLanguage(lang: AppLanguage): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, lang);
  } catch {
    // Language switching must still work when storage is unavailable.
  }
}

function loadEnglishCatalog(): Promise<TranslationMap> {
  if (!englishCatalogPromise) {
    englishCatalogPromise = import("../../components/vkpi/cockpit/data/i18nEn")
      .then(({ I18N_EN }) => I18N_EN as TranslationMap)
      .catch((error) => {
        englishCatalogPromise = null;
        throw error;
      });
  }
  return englishCatalogPromise;
}

function interpolate(template: string, values?: TranslationValues): string {
  if (!values) return template;
  return template.replace(/\{([A-Za-z0-9_]+)\}/g, (match, key: string) => {
    return Object.prototype.hasOwnProperty.call(values, key)
      ? String(values[key])
      : match;
  });
}

function translateChinese(source: string): string {
  const localized = I18N_ZH[source] ?? source;
  return ZH_SOFTEN[localized as keyof typeof ZH_SOFTEN] ?? localized;
}

export function makeT(
  lang: AppLanguage | string,
  englishCatalog: TranslationMap = {},
): Translate {
  return (source, values) => {
    const translated =
      lang === "en"
        ? englishCatalog[source] ?? source
        : translateChinese(source);
    return interpolate(translated, values);
  };
}

export const I18nContext = createContext<I18nContextValue>({
  lang: "zh",
  setLang: () => {},
  t: makeT("zh"),
});

export function LocaleProvider({ children }: PropsWithChildren) {
  const [initialLanguage] = useState<AppLanguage>(readInitialLanguage);
  const [requestedLanguage, setRequestedLanguage] = useState<AppLanguage>(initialLanguage);
  const [lang, setActiveLanguage] = useState<AppLanguage>(
    initialLanguage === "en" ? "zh" : initialLanguage,
  );
  const [englishCatalog, setEnglishCatalog] = useState<TranslationMap | null>(null);

  useEffect(() => {
    if (requestedLanguage === "zh") {
      setActiveLanguage("zh");
      persistLanguage("zh");
      return;
    }
    if (englishCatalog) {
      setActiveLanguage("en");
      persistLanguage("en");
      return;
    }

    let cancelled = false;
    void loadEnglishCatalog()
      .then((catalog) => {
        if (!cancelled) setEnglishCatalog(catalog);
      })
      .catch(() => {
        if (!cancelled) setRequestedLanguage("zh");
      });
    return () => {
      cancelled = true;
    };
  }, [englishCatalog, requestedLanguage]);

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    }
  }, [lang]);

  const setLang = useCallback<LanguageSetter>((next) => {
    setRequestedLanguage((current) => {
      const resolved = typeof next === "function" ? next(current) : next;
      return normalizeLanguage(resolved) ?? "zh";
    });
  }, []);

  const toggleLang = useCallback(() => {
    setRequestedLanguage((current) => (current === "zh" ? "en" : "zh"));
  }, []);

  const t = useMemo(
    () => makeT(lang, englishCatalog ?? undefined),
    [englishCatalog, lang],
  );

  const value = useMemo<I18nContextValue>(
    () => ({
      lang,
      languageLoading: requestedLanguage !== lang,
      setLang,
      toggleLang,
      t,
    }),
    [lang, requestedLanguage, setLang, t, toggleLang],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useT(): I18nContextValue {
  return useContext(I18nContext);
}

export function useLocale(): Required<I18nContextValue> {
  const value = useT();
  return {
    ...value,
    languageLoading: value.languageLoading ?? false,
    toggleLang:
      value.toggleLang ??
      (() => value.setLang((current) => (current === "zh" ? "en" : "zh"))),
  };
}
