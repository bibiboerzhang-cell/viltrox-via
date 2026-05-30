// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { I18N_EN } from "../data/i18nEn";

const e = React.createElement;

export const I18nContext = React.createContext({
  t: (zh) => zh,
  lang: "zh",
  setLang: () => {},
});

export function useT() {
  return React.useContext(I18nContext);
}

export function makeT(lang) {
  return function t(zh) { return lang === "en" ? (I18N_EN[zh] ?? zh) : zh; };
}
