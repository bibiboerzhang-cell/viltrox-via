import { describe, it, expect } from "vitest";

// W5 recon: vkpiDataUtils 纯工具(无网络)。覆盖 safeNumber/format/label、JSON 解析、
// platform 归一、contactLinks 去抽取、stage 前后导航。
import {
  safeNumber,
  formatMoneyCents,
  compactCount,
  rateLabel,
  scoreLabel,
  pickText,
  textValue,
  objectValue,
  arrayValue,
  contactLinksFrom,
  platformFromRaw,
  platformDisplay,
  coerceProjectStage,
  nextProjectStage,
  previousProjectStage,
} from "./vkpiDataUtils";

describe("safeNumber", () => {
  it("非数字/NaN → 0", () => {
    expect(safeNumber("abc")).toBe(0);
    expect(safeNumber(NaN)).toBe(0);
    expect(safeNumber(undefined)).toBe(0);
  });
  it("数字字符串可解析", () => {
    expect(safeNumber("42")).toBe(42);
  });
});

describe("formatMoneyCents / compactCount / rateLabel / scoreLabel", () => {
  it("cents→USD(currencyFormatter 配 maximumFractionDigits:0,无小数)", () => {
    expect(formatMoneyCents(123400)).toBe("$1,234");
  });
  it("compactCount 0/空 → '-'", () => {
    expect(compactCount(0)).toBe("-");
    expect(compactCount(1500)).toBe("1.5K");
  });
  it("rateLabel 百分比两位,0 → '-'", () => {
    expect(rateLabel(0.1234)).toBe("12.34%");
    expect(rateLabel(0)).toBe("-");
  });
  it("scoreLabel n/100,0 → '-'", () => {
    expect(scoreLabel(87.6)).toBe("88/100");
    expect(scoreLabel(0)).toBe("-");
  });
});

describe("pickText / textValue", () => {
  it("pickText 取首个非空,全空用 fallback", () => {
    expect(pickText("默认", "", null, "命中")).toBe("命中");
    expect(pickText("默认", "", null, undefined)).toBe("默认");
  });
  it("textValue 空白 → fallback", () => {
    expect(textValue("  ", "x")).toBe("x");
    expect(textValue("有值")).toBe("有值");
  });
});

describe("objectValue / arrayValue(JSON 容错)", () => {
  it("objectValue 接受对象/JSON 串,拒绝数组与坏串", () => {
    expect(objectValue({ a: 1 })).toEqual({ a: 1 });
    expect(objectValue('{"b":2}')).toEqual({ b: 2 });
    expect(objectValue("[1,2]")).toEqual({});
    expect(objectValue("{bad")).toEqual({});
  });
  it("arrayValue 接受数组/JSON 数组串,其它 → []", () => {
    expect(arrayValue([1, 2])).toEqual([1, 2]);
    expect(arrayValue("[3,4]")).toEqual([3, 4]);
    expect(arrayValue('{"x":1}')).toEqual([]);
    expect(arrayValue("oops")).toEqual([]);
  });
});

describe("contactLinksFrom", () => {
  it("纯邮箱字符串 → label Email", () => {
    const links = contactLinksFrom(["hi@x.com"]);
    expect(links[0].label).toBe("Email");
    expect(links[0].url).toBeUndefined();
  });
  it("http 字符串 → 带 url", () => {
    const links = contactLinksFrom(["https://x.com"]);
    expect(links[0].url).toBe("https://x.com");
    expect(links[0].label).toBe("链接");
  });
  it("对象项取 url/value/label", () => {
    const links = contactLinksFrom([{ label: "主页", url: "https://y.com", value: "y" }]);
    expect(links[0]).toMatchObject({ label: "主页", url: "https://y.com" });
  });
  it("既无 url 又无 value 的对象被跳过", () => {
    expect(contactLinksFrom([{ foo: "bar" }])).toEqual([]);
  });
});

describe("platformFromRaw / platformDisplay", () => {
  it.each([
    ["youtube", "YouTube"],
    ["yt", "YouTube"],
    ["instagram", "Instagram"],
    ["tiktok", "TikTok"],
    ["xiaohongshu", "XHS"],
    ["抖音", "Douyin"],
    ["知乎", "Zhihu"],
    ["twitter", "X"],
    ["unknownthing", "Other"],
  ])("%s → %s", (raw, expected) => {
    expect(platformFromRaw(raw)).toBe(expected);
  });
  it("platformDisplay 走中文标签表(XHS→小红书)", () => {
    expect(platformDisplay("xhs")).toBe("小红书");
  });
});

describe("project stage 导航", () => {
  it("coerceProjectStage: posted→published,未知→discovery", () => {
    expect(coerceProjectStage("posted")).toBe("published");
    expect(coerceProjectStage("garbage")).toBe("discovery");
    expect(coerceProjectStage("contacted")).toBe("contacted");
  });
  it("nextProjectStage 推进一步,末位 → null", () => {
    expect(nextProjectStage("discovery")).toBe("contacted");
    expect(nextProjectStage("closed")).toBeNull();
  });
  it("previousProjectStage 回退一步,首位 → null", () => {
    expect(previousProjectStage("contacted")).toBe("discovery");
    expect(previousProjectStage("discovery")).toBeNull();
  });
});
