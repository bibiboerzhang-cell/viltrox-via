import { describe, it, expect } from "vitest";

// W5 recon: i18n.makeT —— zh 走 ZH_SOFTEN,en 走 I18N_EN,未命中原样返回。
import { makeT } from "./i18n";
import { ZH_SOFTEN } from "../data/zhSoften";
import { I18N_EN } from "../data/i18nEn";
import { I18N_ZH } from "../data/i18nZh";

describe("makeT(lang='zh')", () => {
  const t = makeT("zh");
  it("命中软化表 → 返回软化串", () => {
    expect(t("AI 决策洞察")).toBe(ZH_SOFTEN["AI 决策洞察"]);
  });
  it("未命中 → 原样返回", () => {
    expect(t("没有登记的中文")).toBe("没有登记的中文");
  });
  it("英文源 label 统一转中文并保留技术/品牌缩写", () => {
    expect(t("Dashboard")).toBe(I18N_ZH.Dashboard);
    expect(t("KOL Pool")).toBe("KOL 人才库");
    expect(t("Shopify")).toBe("Shopify");
    expect(t("GTM Command")).toBe("GTM 指挥台");
    expect(t("Active Roster")).toBe("达人总数");
    expect(t("Active 30D")).toBe("近 30 天活跃");
    expect(t("Total Exposure")).toBe("总曝光量");
    expect(t("Engagement Rate")).toBe("互动率");
    expect(t("Attributed GMV")).toBe("归因 GMV");
    expect(t("Avg ROI")).toBe("平均 ROI");
  });
});

describe("makeT(lang='en')", () => {
  const t = makeT("en", I18N_EN);
  it("命中英文表 → 返回英文", () => {
    expect(t("Dashboard")).toBe(I18N_EN["Dashboard"]);
    expect(t("取消")).toBe("Cancel");
    expect(t("增长总览")).toBe("Growth Overview");
    expect(t("KOL 档案")).toBe("KOL Profile");
  });
  it("未命中 → 原样返回中文 key", () => {
    expect(t("没有英文翻译的串")).toBe("没有英文翻译的串");
  });
  it("en 模式不走软化表(与 zh 分流)", () => {
    // 'AI 决策洞察' 在 ZH_SOFTEN 有,但 en 不查它
    expect(t("AI 决策洞察")).toBe("AI 决策洞察");
  });
  it("英文词典尚未按需加载时安全回退原文", () => {
    expect(makeT("en")("Dashboard")).toBe("Dashboard");
  });
});
