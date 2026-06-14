// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { I18N_EN } from "../data/i18nEn";
import { ZH_SOFTEN } from "../data/zhSoften";

const e = React.createElement;

export const I18nContext = React.createContext({
  t: (zh) => ZH_SOFTEN[zh] ?? zh,
  lang: "zh",
  setLang: () => {},
});

export function useT() {
  return React.useContext(I18nContext);
}

export function makeT(lang) {
  // zh:走员工友好措辞表(去 AI 机器感 / KPI 考核压力);en:走英文表。
  return function t(zh) { return lang === "en" ? (I18N_EN[zh] ?? zh) : (ZH_SOFTEN[zh] ?? zh); };
}
