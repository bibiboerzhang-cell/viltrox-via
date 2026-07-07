import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// U3 · NorthStarGauges 渲染单测:①三表盘出数(真端点形状)②表缺诚实徽标
// ③端点错/形状缺 → 安静降级一行 ④无 token 不拉取不渲染。

const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { NorthStarGauges, normalizeNorthstar } from "./NorthStarGauges";

const e = React.createElement;

// 真端点冒烟形状(2026-07-07 本地真库:brief=1 / dealer=0 / 裁决 0%)。
const NORTHSTAR_RAW = {
  status: "ok",
  window_days: 90,
  generated_at: "2026-07-07T03:00:00+00:00",
  metrics: {
    launch_briefs: { label: "Launch Brief", value: 1, target: 30, unit: "份", status: "ok", note: "" },
    dealers: { label: "Dealer", value: 0, target: 300, unit: "行", status: "ok", note: "" },
    verdict_rate: {
      label: "GTM 裁决率", value: 0, target: 30, unit: "%",
      status: "table_missing", note: "vkpi_gtm_outcomes 未建,诚实 0%", decided: 0, total: 0,
    },
  },
};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue(NORTHSTAR_RAW);
});

describe("normalizeNorthstar", () => {
  it("完整响应 → 三指标齐;error/形状缺 → null", () => {
    const parsed = normalizeNorthstar(NORTHSTAR_RAW);
    expect(parsed?.metrics.map((m) => m.key)).toEqual(["launch_briefs", "dealers", "verdict_rate"]);
    expect(parsed?.metrics[0].value).toBe(1);
    expect(normalizeNorthstar({ status: "error", reason: "boom" })).toBeNull();
    expect(normalizeNorthstar({})).toBeNull();
    expect(normalizeNorthstar(null)).toBeNull();
  });
});

describe("NorthStarGauges", () => {
  it("三表盘出数 + 表缺诚实徽标 + 裁决明细", async () => {
    render(e(NorthStarGauges, { apiToken: "tok" }));
    expect(await screen.findByText("90 天北极星")).toBeInTheDocument();
    expect(screen.getByTestId("northstar-gauge-launch_briefs")).toBeInTheDocument();
    expect(screen.getByTestId("northstar-gauge-dealers")).toBeInTheDocument();
    expect(screen.getByTestId("northstar-gauge-verdict_rate")).toBeInTheDocument();
    expect(screen.getByText("1 / 30 份")).toBeInTheDocument();
    expect(screen.getByText("0 / 300 行")).toBeInTheDocument();
    expect(screen.getByText("0% / 30%")).toBeInTheDocument();
    // 诚实展示欠账:表缺徽标 + 明细 0/0
    expect(screen.getByText("表缺")).toBeInTheDocument();
    expect(screen.getByText("0/0 已裁决")).toBeInTheDocument();
    expect(screen.getByText(/生成于 2026-07-07T03:00:00/)).toBeInTheDocument();
    const call = apiFetch.mock.calls[0];
    expect(String(call[0])).toBe("/api/admin/vkpi/gtm/northstar");
  });

  it("端点回错误 / 形状缺 → 安静降级一行,不炸", async () => {
    apiFetch.mockResolvedValue({ status: "error", reason: "db down" });
    render(e(NorthStarGauges, { apiToken: "tok" }));
    expect(await screen.findByText(/北极星端点暂不可用/)).toBeInTheDocument();
  });

  it("请求 reject → 同样安静降级", async () => {
    apiFetch.mockRejectedValue(new Error("500"));
    render(e(NorthStarGauges, { apiToken: "tok" }));
    expect(await screen.findByText(/北极星端点暂不可用/)).toBeInTheDocument();
  });

  it("无 token → 不渲染不拉取", () => {
    const { container } = render(e(NorthStarGauges, { apiToken: "" }));
    expect(container.firstChild).toBeNull();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
