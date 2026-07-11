import { describe, it, expect } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";
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

  it("四个平台样例展示格式、作者、发布、新鲜度、来源和原帖", () => {
    render(React.createElement(SignalDetailModal, {
      alert: {
        id: "social-sources",
        severity: "medium",
        title: "外部平台内容样例",
        desc: "用于验证来源元数据",
        time: "2 小时前",
        trendPct: "上升",
        sources: [
          {
            name: "YouTube source",
            platform: "youtube",
            content_origin: "external",
            url: "https://youtube.com/shorts/one",
            author: "YT Author",
            published_at: "2026-07-09",
            freshness_label: "1 天前",
            thumbnail_url: "https://img.example/one.jpg",
            sourceTable: "vkpi_market_mentions",
            sourceId: 1,
          },
          {
            name: "TikTok source",
            platform: "tiktok",
            content_origin: "external",
            url: "https://tiktok.com/@author/video/2",
            author: "TT Author",
            published_at: "2026-07-08",
            sourceTable: "vkpi_market_mentions",
            sourceId: 2,
          },
          {
            name: "Instagram source",
            platform: "instagram",
            content_origin: "external",
            url: "https://instagram.com/reel/three",
            author: "IG Author",
            published_at: "2026-07-07",
            sourceTable: "vkpi_market_mentions",
            sourceId: 3,
          },
          {
            name: "Facebook source",
            platform: "facebook",
            content_origin: "external",
            author: "FB Author",
            sourceTable: "vkpi_market_mentions",
            sourceId: 4,
          },
        ],
      },
      onClose: () => {},
    }));

    expect(screen.getAllByText("外部市场样例")).toHaveLength(4);
    expect(screen.getByText("YouTube")).toBeTruthy();
    expect(screen.getByText("TikTok")).toBeTruthy();
    expect(screen.getByText("Instagram")).toBeTruthy();
    expect(screen.getByText("Facebook")).toBeTruthy();
    expect(screen.getByText("Shorts")).toBeTruthy();
    expect(screen.getByText("短视频")).toBeTruthy();
    expect(screen.getByText("Reel")).toBeTruthy();
    expect(screen.getByText("YT Author")).toBeTruthy();
    expect(screen.getByText("vkpi_market_mentions:1")).toBeTruthy();
    expect(screen.getByRole("link", { name: /YouTube source 原帖/ })).toHaveAttribute("href", "https://youtube.com/shorts/one");
    expect(screen.getByText("无原始链接")).toBeTruthy();
  });

  it("完全无来源时明确说明无法核验", () => {
    render(React.createElement(SignalDetailModal, {
      alert: { id: "none", severity: "info", title: "无来源信号", desc: "仅有摘要", sources: [] },
      onClose: () => {},
    }));

    expect(screen.getByText(/未保留来源记录/)).toBeTruthy();
    expect(screen.getByText(/无可回跳来源/)).toBeTruthy();
  });
});
