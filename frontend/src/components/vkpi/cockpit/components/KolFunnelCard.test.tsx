import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// U4:KolFunnelCard 冒烟(纯 props,不打后端)。
// 覆盖:真实态漏斗条入场(data-stage)+ AnimatedNumber count-up + 待后端空态。
import { KolFunnelCard } from "./KolFunnelCard";

const FUNNEL = {
  isReal: true,
  stages: [
    { key: "favorites", label: "收藏", count: 120 },
    { key: "claimed", label: "认领", count: 60 },
    { key: "in_project", label: "入项目", count: 30 },
    { key: "published", label: "已发布", count: 12 },
  ],
  byStaff: [
    { staff_id: 1, name: "小明", favorites: 80, in_project: 20 },
  ],
};

describe("KolFunnelCard KOL 漏斗", () => {
  it("真实态:四段漏斗条入场渲染 + 数字 count-up 到终值", async () => {
    const { container } = render(<KolFunnelCard funnel={FUNNEL} />);

    expect(screen.getByText("KOL 漏斗")).toBeInTheDocument();
    expect(screen.getByText("真实")).toBeInTheDocument();
    expect(screen.getByText("1.收藏")).toBeInTheDocument();
    expect(screen.getByText("4.已发布")).toBeInTheDocument();

    // 动画属性断言:四段填充条各带 data-stage(motion 宽度入场载体)。
    expect(container.querySelectorAll("[data-stage]").length).toBe(4);
    // 数字走 AnimatedNumber(带 data-animated-number 标记)。
    expect(container.querySelectorAll("[data-animated-number]").length).toBe(4);

    // count-up 到终值(300ms + stagger,放宽窗口)。
    await waitFor(
      () => expect(screen.getByText("120")).toBeInTheDocument(),
      { timeout: 3000 },
    );
    await waitFor(
      () => expect(screen.getByText("12")).toBeInTheDocument(),
      { timeout: 3000 },
    );
  });

  it("count == null → 显示 --,不编数", () => {
    render(
      <KolFunnelCard
        funnel={{ isReal: true, stages: [{ key: "favorites", label: "收藏", count: null }], byStaff: [] }}
      />,
    );
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("非真实态:诚实空态「漏斗数据待后端」", () => {
    render(<KolFunnelCard funnel={{ isReal: false, stages: [], byStaff: [] }} />);
    expect(screen.getByText("漏斗数据待后端")).toBeInTheDocument();
    expect(screen.getByText("待后端")).toBeInTheDocument();
  });
});
