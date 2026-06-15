import { describe, it, expect } from "vitest";
import React from "react";
import { render } from "@testing-library/react";
import { SignalsAllModal } from "./SignalsAllModal";
import { SignalsAlertsCard } from "../SignalsAlertsCard";

// 混合:无 sources(此前崩点)/ 字符串源 / 对象源 / sources 非数组。
const alerts = [
  { id: "s1", severity: "high", title: "无源信号", desc: "x", time: "1分钟前", totalMentions: 4, trendPct: "78%" },
  { id: "s2", severity: "low", title: "字符串源", desc: "y", time: "2分钟前", sources: ["GoogleNews", "Reddit"], totalMentions: 3, trendPct: "50%" },
  { id: "s3", severity: "info", title: "对象源", desc: "z", time: "3分钟前", sources: [{ name: "DPReview" }], totalMentions: 1, trendPct: "10%" },
  { id: "s4", severity: "medium", title: "脏源", desc: "w", time: "4分钟前", sources: "not-an-array", totalMentions: 2, trendPct: "20%" },
];

describe("Signals 防 sources 缺失崩页(此前 a.sources.map → RouteErrorBoundary 整页失败)", () => {
  it("SignalsAllModal 渲染混合 sources 不抛", () => {
    expect(() =>
      render(React.createElement(SignalsAllModal, { alerts, onClose: () => {}, onAlertClick: () => {} })),
    ).not.toThrow();
  });

  it("SignalsAlertsCard 面板渲染混合 sources 不抛", () => {
    expect(() =>
      render(React.createElement(SignalsAlertsCard, { alerts, onAlertClick: () => {}, onViewAll: () => {} })),
    ).not.toThrow();
  });
});
