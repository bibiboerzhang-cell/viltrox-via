import { describe, expect, it } from "vitest";
import { adaptiveRowCount } from "./moduleSize";

// adaptiveRowCount 纯函数契约:可用高度 → 可见行数,夹在 [min, max],
// 垃圾入参回保底下限——列表模块直接拿返回值喂 slice(0, n),永不为负。
describe("adaptiveRowCount", () => {
  it("按可用高度取整算行数(KOL 库真实几何:26 格高 → 17 行)", () => {
    // 26 格 × 22px + 25 × 14px 间距 = 922px;chrome 210,行距 41 → floor(712/41)=17
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: 41, min: 6, max: 40 })).toBe(17);
  });

  it("高度不足时夹到 min(默认卡高不缩水旧 6 行体验)", () => {
    // 10 格 × 22 + 9 × 14 = 346px → floor(136/41)=3 → 抬到 min 6
    expect(adaptiveRowCount({ heightPx: 346, chromePx: 210, rowPx: 41, min: 6, max: 40 })).toBe(6);
    expect(adaptiveRowCount({ heightPx: 0, chromePx: 210, rowPx: 41, min: 6, max: 40 })).toBe(6);
  });

  it("行数封顶 max(数据只有这么多,不虚报)", () => {
    expect(adaptiveRowCount({ heightPx: 5000, chromePx: 210, rowPx: 41, min: 6, max: 9 })).toBe(9);
  });

  it("max < min 以 max 为准(3 条数据绝不显示 6 行)", () => {
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: 41, min: 6, max: 3 })).toBe(3);
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: 41, min: 6, max: 0 })).toBe(0);
  });

  it("min=0 时允许 0 行(内容墙 rowsFit 场景)", () => {
    expect(adaptiveRowCount({ heightPx: 454, chromePx: 288, rowPx: 244, min: 0, max: 60 })).toBe(0);
    expect(adaptiveRowCount({ heightPx: 1066, chromePx: 288, rowPx: 244, min: 0, max: 60 })).toBe(3);
  });

  it("非有限入参 / rowPx<=0 回保底下限,绝不 NaN 或负数", () => {
    expect(adaptiveRowCount({ heightPx: Number.NaN, chromePx: 210, rowPx: 41, min: 6, max: 40 })).toBe(6);
    expect(adaptiveRowCount({ heightPx: 922, chromePx: Number.POSITIVE_INFINITY, rowPx: 41, min: 6, max: 40 })).toBe(6);
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: 0, min: 6, max: 40 })).toBe(6);
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: -5, min: 6, max: 40 })).toBe(6);
    expect(adaptiveRowCount({ heightPx: 922, chromePx: 210, rowPx: 41, min: Number.NaN, max: Number.NaN })).toBe(0);
  });

  it("小数 min/max 向下取整(行数必须是整数)", () => {
    expect(adaptiveRowCount({ heightPx: 5000, chromePx: 210, rowPx: 41, min: 5.9, max: 8.7 })).toBe(8);
    expect(adaptiveRowCount({ heightPx: 0, chromePx: 210, rowPx: 41, min: 5.9, max: 8.7 })).toBe(5);
  });
});
