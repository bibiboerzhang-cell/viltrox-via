import { describe, it, expect } from "vitest";

// W5 recon: i18n.makeT —— zh 走 ZH_SOFTEN,en 走 I18N_EN,未命中原样返回。
import { makeT } from "./i18n";
import { ZH_SOFTEN } from "../data/zhSoften";
import { I18N_EN } from "../data/i18nEn";

describe("makeT(lang='zh')", () => {
  const t = makeT("zh");
  it("命中软化表 → 返回软化串", () => {
    expect(t("AI 决策洞察")).toBe(ZH_SOFTEN["AI 决策洞察"]);
  });
  it("未命中 → 原样返回", () => {
    expect(t("没有登记的中文")).toBe("没有登记的中文");
  });
});

describe("makeT(lang='en')", () => {
  const t = makeT("en");
  it("命中英文表 → 返回英文", () => {
    expect(t("Dashboard")).toBe(I18N_EN["Dashboard"]);
    expect(t("取消")).toBe("Cancel");
  });
  it("未命中 → 原样返回中文 key", () => {
    expect(t("没有英文翻译的串")).toBe("没有英文翻译的串");
  });
  it("en 模式不走软化表(与 zh 分流)", () => {
    // 'AI 决策洞察' 在 ZH_SOFTEN 有,但 en 不查它
    expect(t("AI 决策洞察")).toBe("AI 决策洞察");
  });
});
