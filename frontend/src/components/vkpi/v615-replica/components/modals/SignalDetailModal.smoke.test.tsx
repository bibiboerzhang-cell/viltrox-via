import { describe, it, expect } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import { SignalDetailModal } from "./SignalDetailModal";

// normalizeSignals 产的信号没有 impact/actions/summary、sources 无 mentions —— 此前点击 → impact.map 崩。
const normalizedSignal = {
  id: "s1",
  severity: "high",
  title: "INSTA360 本轮信号较突出",
  desc: "竞品 INSTA360 本轮出现 3 条、累计热度 score 143",
  time: "1 分钟前",
  sources: [{ name: "market-intelligence", url: "" }],
  totalMentions: 3,
  trendPct: "72%",
  raw: {},
};

// 竞品雷达条:impact 在 raw 里(应兜底显示),sources 无 mentions。
const radarItem = {
  id: "radar-0",
  severity: "high",
  title: "🛰 Samyang:AF 14-24mm F2.8 L 发布",
  desc: "发布 · 对我们:威胁 — 施耐德联名 + 极致轻量",
  time: "今日",
  sources: [{ name: "竞品雷达 · Gemini 接地", url: "" }],
  totalMentions: 0,
  trendPct: "威胁",
  raw: { brand: "Samyang", impact: "威胁:施耐德联名 + 极致轻量挑战 Viltrox 中高端变焦" },
};

describe("SignalDetailModal 点击跳转防崩", () => {
  it("normalizeSignals 信号(无 impact/actions/mentions)挂载不抛", () => {
    expect(() =>
      render(React.createElement(SignalDetailModal, { alert: normalizedSignal, onClose: () => {} })),
    ).not.toThrow();
  });

  it("竞品雷达条挂载不抛", () => {
    expect(() =>
      render(React.createElement(SignalDetailModal, { alert: radarItem, onClose: () => {} })),
    ).not.toThrow();
  });
});
