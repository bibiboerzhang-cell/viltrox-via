import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import enCommon from "./locales/en/common.json";
import zhCommon from "./locales/zh/common.json";

export const LANGUAGE_STORAGE_KEY = "viltrox-ui-language";
export const SUPPORTED_LANGUAGES = ["en", "zh"] as const;

const resources = {
  en: { translation: enCommon },
  zh: { translation: zhCommon },
} as const;

if (typeof window !== "undefined") {
  const shouldMuteLocizeNotice = (args: unknown[]) =>
    (() => {
      const message = args
        .map((value) => String(value ?? ""))
        .join(" ")
        .toLowerCase();
      return message.includes("locize") && message.includes("i18next");
    })();

  (["info", "log", "warn"] as const).forEach((method) => {
    const original = window.console[method].bind(window.console);
    window.console[method] = (...args: unknown[]) => {
      if (shouldMuteLocizeNotice(args)) {
        return;
      }
      original(...args);
    };
  });
}

function resolveLanguage() {
  const savedLanguage = String(globalThis.localStorage?.getItem(LANGUAGE_STORAGE_KEY) || "").toLowerCase();
  if (savedLanguage === "en" || savedLanguage === "zh") {
    return savedLanguage;
  }
  const browserLanguage = String(globalThis.navigator?.language || "en").toLowerCase();
  if (browserLanguage.startsWith("zh")) {
    return "zh";
  }
  return "en";
}

void i18n.use(initReactI18next).init({
  resources,
  lng: resolveLanguage(),
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
});

export default i18n;
