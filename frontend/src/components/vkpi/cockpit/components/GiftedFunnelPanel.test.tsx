import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// U4:GiftedFunnelPanel 履约漏斗冒烟。seam = apiFetch(hermetic,不打后端)。
// 覆盖:四段漏斗条渲染(data-stage)+ 超期红段单次脉冲标记(data-pulse-once)
// + AnimatedNumber count-up 到终值 + 超期红名单 + 诚实空态。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
}));

import { GiftedFunnelPanel } from "./GiftedFunnelPanel";

beforeEach(() => {
  apiFetch.mockReset();
});

const FUNNEL = {
  status: "ok",
  overdue_days: 21,
  gifted: 40,
  posted: 18,
  waiting: 15,
  overdue: 7,
  post_rate: 0.45,
  stages: [
    { key: "gifted", label: "已送样", n: 40 },
    { key: "posted", label: "已发布", n: 18 },
    { key: "waiting", label: "观察中", n: 15 },
    { key: "overdue", label: "超期未发", n: 7 },
  ],
  overdue_items: [
    {
      assignment_id: 1, project_id: 3, project_name: "P1 新品送测",
      kol_pool_id: 9, kol_name: "Alice", platform: "youtube",
      sent_at: "2026-06-07", sent_basis: "shipped_at", days_since_sent: 30,
      suggested_action: "催更",
    },
  ],
  basis_note: "口径:送样=派单 stage;发布=证据。",
};

describe("GiftedFunnelPanel 履约漏斗", () => {
  it("四段漏斗条入场渲染 + 超期红段带单次脉冲标记", async () => {
    apiFetch.mockResolvedValue(FUNNEL);
    const { container } = render(<GiftedFunnelPanel apiToken="tok" />);

    expect(await screen.findByText("送样 → 发布 履约漏斗")).toBeInTheDocument();
    expect(screen.getByText("发布率 45.0%")).toBeInTheDocument();
    expect(screen.getByText("超期 ×7")).toBeInTheDocument();

    // 动画属性断言:四段填充条各带 data-stage;仅超期红段带 data-pulse-once
    // (一次性脉冲标记,绝无循环动画)。
    const bars = container.querySelectorAll("[data-stage]");
    expect(bars.length).toBe(4);
    const overdueBar = container.querySelector('[data-stage="overdue"]');
    expect(overdueBar).not.toBeNull();
    expect(overdueBar!.getAttribute("data-pulse-once")).toBe("true");
    const giftedBar = container.querySelector('[data-stage="gifted"]');
    expect(giftedBar!.hasAttribute("data-pulse-once")).toBe(false);

    // 数字 count-up 到终值(AnimatedNumber,含 stagger 延迟,放宽窗口)。
    await waitFor(
      () => expect(screen.getByText("40")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    await waitFor(
      () => expect(screen.getByText("18")).toBeInTheDocument(),
      { timeout: 3000 },
    );

    // 超期红名单行。
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("30天")).toBeInTheDocument();
  });

  it("status=empty → 如实展示 reason", async () => {
    apiFetch.mockResolvedValue({ status: "empty", reason: "暂无送样派单记录" });
    render(<GiftedFunnelPanel apiToken="tok" />);
    expect(await screen.findByText("暂无送样派单记录")).toBeInTheDocument();
  });

  it("接口失败 → 整块安静缺席", async () => {
    apiFetch.mockRejectedValue(new Error("boom"));
    const { container } = render(<GiftedFunnelPanel apiToken="tok" />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
