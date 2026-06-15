// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { I18N_EN } from "../data/i18nEn";
import { ZH_SOFTEN } from "../data/zhSoften";

export const I18nContext = React.createContext({
  t: (zh: string) => (ZH_SOFTEN as any)[zh] ?? zh,
  lang: "zh",
  setLang: (_lang: string) => {},
});

export function useT() {
  return React.useContext(I18nContext);
}

export function makeT(lang: string) {
  // zh:走成员友好措辞表(去 AI 机器感 / KPI 考核压力);en:走英文表。
  return function t(zh: string) { return lang === "en" ? ((I18N_EN as any)[zh] ?? zh) : ((ZH_SOFTEN as any)[zh] ?? zh); };
}
