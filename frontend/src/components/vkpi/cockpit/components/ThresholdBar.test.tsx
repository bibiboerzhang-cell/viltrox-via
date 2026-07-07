import { describe, it, expect } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// U3 · ThresholdBar 单测:①文本量化值解析(时间窗不算阈值)②同单位 → 单条三色区间条
// ③异单位 → 每条件 mini 阈值带 ④全解析不出 → 渲染 null(调用方保留文本卡,不硬编)。

import { ThresholdBar, parseThresholdValue } from "./ThresholdBar";

const e = React.createElement;

describe("parseThresholdValue · 条件文本量化值解析", () => {
  it("跳过时间窗(48h),取第一个数+量词", () => {
    expect(parseThresholdValue("48h 内 3 条视频完播进前 25%")).toEqual({ value: 3, unit: "条", raw: "3 条" });
  });

  it("百分比", () => {
    expect(parseThresholdValue("回复率 ≥ 20%")).toEqual({ value: 20, unit: "%", raw: "20%" });
    expect(parseThresholdValue("转化率低于 1.5%")).toEqual({ value: 1.5, unit: "%", raw: "1.5%" });
  });

  it("美元金额", () => {
    expect(parseThresholdValue("单条成本超 $250 即撤")).toEqual({ value: 250, unit: "$", raw: "$250" });
  });

  it("数+量词(个/家)", () => {
    expect(parseThresholdValue("10 个 KOL 有效回复低于目标")).toEqual({ value: 10, unit: "个", raw: "10 个" });
    expect(parseThresholdValue("新增 5 家 Dealer 意向")).toEqual({ value: 5, unit: "家", raw: "5 家" });
  });

  it("无量化值 → null(诚实退化)", () => {
    expect(parseThresholdValue("完播率骤降且评论转负")).toBeNull();
    expect(parseThresholdValue("")).toBeNull();
  });
});

describe("ThresholdBar · 渲染形态", () => {
  it("同单位双条件 → 单条三色区间条,加码/撤退线标注", () => {
    render(e(ThresholdBar, { escalateIf: "回复率 ≥ 20%", retreatIf: "回复率跌破 8%" }));
    expect(screen.getByTestId("threshold-bar")).toBeInTheDocument();
    expect(screen.getByText("加码线 20%")).toBeInTheDocument();
    expect(screen.getByText("撤退线 8%")).toBeInTheDocument();
    expect(screen.getByText(/显示层解析/)).toBeInTheDocument();
  });

  it("异单位 → 每条件一条 mini 阈值带", () => {
    render(
      e(ThresholdBar, {
        escalateIf: "48h 内 3 条视频完播进前 25%",
        retreatIf: "10 个 KOL 有效回复低于目标",
      }),
    );
    expect(screen.getByTestId("threshold-bar")).toBeInTheDocument();
    expect(screen.getByTestId("threshold-strip-escalate")).toBeInTheDocument();
    expect(screen.getByTestId("threshold-strip-retreat")).toBeInTheDocument();
    expect(screen.getByText("3条")).toBeInTheDocument();
    expect(screen.getByText("10个")).toBeInTheDocument();
  });

  it("只解析出一侧 → 只出该侧 mini 带", () => {
    render(e(ThresholdBar, { escalateIf: "热度上行就加码", retreatIf: "回复率跌破 8%" }));
    expect(screen.queryByTestId("threshold-strip-escalate")).toBeNull();
    expect(screen.getByTestId("threshold-strip-retreat")).toBeInTheDocument();
  });

  it("全解析不出 → 渲染 null(文本卡兜底在调用方,不硬编)", () => {
    const { container } = render(e(ThresholdBar, { escalateIf: "看情况", retreatIf: "" }));
    expect(container.firstChild).toBeNull();
  });
});
