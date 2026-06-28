import { describe, it, expect } from "vitest";
import { formatNumber, formatPercent, formatMoney } from "./format";

// P2-6: 卡面格式化纯函数边界(null/''/NaN/紧凑量级/百分比 digits/金额分→元)。

describe("formatNumber 边界", () => {
  it("null/undefined → 破折号", () => {
    expect(formatNumber(null)).toBe("—");
    expect(formatNumber(undefined)).toBe("—");
  });
  it("非数字串 → 破折号", () => {
    expect(formatNumber("abc")).toBe("—");
  });
  it("带逗号的串可解析", () => {
    expect(formatNumber("1,500")).toBe("1.5K");
  });
  it("量级:K/M/B", () => {
    expect(formatNumber(1500)).toBe("1.5K");
    expect(formatNumber(2_500_000)).toBe("2.50M");
    expect(formatNumber(3_200_000_000)).toBe("3.20B");
  });
  it("小数值原样字符串", () => {
    expect(formatNumber(42)).toBe("42");
  });
});

describe("formatPercent 边界", () => {
  it("null → 破折号", () => {
    expect(formatPercent(null)).toBe("—");
  });
  it("默认 2 位小数", () => {
    expect(formatPercent(3.14159)).toBe("3.14%");
  });
  it("自定义 digits", () => {
    expect(formatPercent(3.14159, 0)).toBe("3%");
  });
  it("带 % 的串可解析", () => {
    expect(formatPercent("5%", 1)).toBe("5.0%");
  });
  it("NaN 串 → 破折号", () => {
    expect(formatPercent("xx")).toBe("—");
  });
});

describe("formatMoney 分→元", () => {
  it("null → 破折号", () => {
    expect(formatMoney(null)).toBe("—");
  });
  it("小额四舍五入到元", () => {
    expect(formatMoney(12345)).toBe("$123");
  });
  it("千级 → k", () => {
    expect(formatMoney(500000)).toBe("$5k");
  });
  it("百万级 → M", () => {
    expect(formatMoney(250_000_000)).toBe("$2.5M");
  });
});
