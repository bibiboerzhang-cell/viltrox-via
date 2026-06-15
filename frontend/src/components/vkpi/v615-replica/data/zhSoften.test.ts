import { describe, it, expect } from "vitest";

// W5 recon: zhSoften 是整串精确替换表(去 AI 机器感 / KPI 考核压力)。
// 断言关键映射存在、值无残留敏感词、且只做整串匹配(非子串)。
import { ZH_SOFTEN } from "./zhSoften";

describe("ZH_SOFTEN 关键映射", () => {
  it.each([
    ["AI 决策洞察", "今日要点"],
    ["智能层战情室", "协作台"],
    ["排名", "概览"],
    ["末位", "待关注"],
    ["绩效", "工作进展"],
    ["考核", "回顾"],
    ["KPI 总览", "工作概览"],
  ])("%s → %s", (zh, soft) => {
    expect(ZH_SOFTEN[zh]).toBe(soft);
  });
});

describe("软化值不再含敏感原词", () => {
  // 替换后的展示串不应残留这些"机器感/考核压力"词。
  const banned = ["排名", "末位", "考核", "绩效", "战情室"];
  it("所有替换值都不含 banned 词", () => {
    for (const value of Object.values(ZH_SOFTEN)) {
      for (const word of banned) {
        expect(value.includes(word)).toBe(false);
      }
    }
  });
});

describe("整串精确匹配语义(非子串)", () => {
  it("未登记的串不在表中(由 t() 原样返回)", () => {
    expect(ZH_SOFTEN["完全没登记的词"]).toBeUndefined();
  });
  it("'AI 推荐' 与 'AI 推荐待接入' 是两条独立 key", () => {
    expect(ZH_SOFTEN["AI 推荐"]).toBe("为你推荐");
    expect(ZH_SOFTEN["AI 推荐待接入"]).toBe("推荐待接入");
  });
});
