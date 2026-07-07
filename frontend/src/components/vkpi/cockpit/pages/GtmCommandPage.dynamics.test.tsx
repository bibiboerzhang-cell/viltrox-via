import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// U3 · GTM Command 动态化集成测(独立于 GTM-1 的 GtmCommandPage.test,零改他人文件):
//   ①北极星三表盘挂页面顶部 ②preview 加载 → 骨架屏(结束即卸载)
//   ③条件化预判卡出 ThresholdBar(文本卡四段式保留,阈值条只是叠加)。

const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { GtmCommandPage } from "./GtmCommandPage";

const e = React.createElement;

const NORTHSTAR_RAW = {
  status: "ok",
  window_days: 90,
  generated_at: "2026-07-07T03:00:00+00:00",
  metrics: {
    launch_briefs: { label: "Launch Brief", value: 1, target: 30, unit: "份", status: "ok" },
    dealers: { label: "Dealer", value: 0, target: 300, unit: "行", status: "ok" },
    verdict_rate: { label: "GTM 裁决率", value: 0, target: 30, unit: "%", status: "ok", decided: 0, total: 0 },
  },
};

const PREVIEW_RAW = {
  public_plan: {
    thesis: { go_nogo: "GO", market: "US", persona: "人像", mainline: "转化", confidence: "medium", basis_summary: "" },
    forecast: [
      {
        horizon_days: 14,
        statement: "美区 14 天放大机会",
        signals_summary: "完播上行",
        confidence: "medium",
        escalate_if: "48h 内 3 条视频完播进前 25%",
        retreat_if: "10 个 KOL 有效回复低于目标",
      },
      { horizon_days: 30, statement: "只有陈述没有条件" },
    ],
    roadmap: {},
    kol_candidates: { items: [] },
    dealer_targets: { items: [] },
    official_channel_actions: { items: [] },
    shopify_indie_site_actions: { items: [] },
    content_angles: { items: [] },
    budget_mix: { items: [] },
    risks: [],
    data_gaps: [],
    action_inbox_items: [],
    success_metrics: [],
  },
  meta: { generated_at: "2026-07-07T01:00:00Z", coverage: {}, data_gaps: [] },
};

let resolvePreview: (v: unknown) => void = () => {};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockImplementation((url: unknown) => {
    const u = String(url);
    if (u.startsWith("/api/admin/vkpi/gtm/northstar")) return Promise.resolve(NORTHSTAR_RAW);
    if (u.startsWith("/api/admin/vkpi/market-brain/summary")) return Promise.resolve({});
    if (u.startsWith("/api/admin/vkpi/sku/list")) {
      return Promise.resolve({ items: [{ sku: "AF-85MM-F14-PRO-FE", model_name: "AF 85mm F1.4 Pro" }] });
    }
    if (u.startsWith("/api/admin/vkpi/market-brain/gtm-plan/preview")) {
      return new Promise((resolve) => { resolvePreview = resolve; });
    }
    return Promise.resolve({});
  });
});

describe("GtmCommandPage · U3 动态化", () => {
  it("北极星三表盘挂顶部(全局模式即出)", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    expect(await screen.findByText("90 天北极星")).toBeInTheDocument();
    expect(screen.getByTestId("northstar-gauges")).toBeInTheDocument();
    expect(screen.getByText("1 / 30 份")).toBeInTheDocument();
  });

  it("preview 加载 → 骨架屏;返回后骨架卸载,预判卡带阈值条(文本四段式保留)", async () => {
    render(e(GtmCommandPage, { apiToken: "tok" }));
    const input = screen.getByPlaceholderText(/搜索 SKU/);
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "85" } });
    const option = await screen.findByText("AF 85mm F1.4 Pro");
    fireEvent.click(option);

    // 加载中:骨架屏在,旧 spinner 文案仍以脚注形式保留在骨架内
    expect(await screen.findByTestId("preview-skeleton")).toBeInTheDocument();
    expect(screen.getByText(/作战预览聚合中/)).toBeInTheDocument();

    resolvePreview(PREVIEW_RAW);
    await waitFor(() => expect(screen.queryByTestId("preview-skeleton")).toBeNull());

    // 预判卡:四段式文本保留 + 阈值条叠加(第二条无条件 → 无阈值条,文本卡退化)
    expect(await screen.findByText(/48h 内 3 条视频完播进前 25%/)).toBeInTheDocument();
    expect(screen.getByText(/10 个 KOL 有效回复低于目标/)).toBeInTheDocument();
    expect(screen.getAllByTestId("threshold-bar")).toHaveLength(1);
    expect(screen.getByTestId("threshold-strip-escalate")).toBeInTheDocument();
    expect(screen.getByTestId("threshold-strip-retreat")).toBeInTheDocument();
    expect(screen.getAllByText(/条件缺失/).length).toBeGreaterThanOrEqual(2);
  });
});
