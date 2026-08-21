import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { ModuleCard } from "./MarketVoicePage.modules";

describe("shared cockpit module header density", () => {
  it("keeps frequent module labels readable without enlarging the content surface", () => {
    const { container } = render(
      <ModuleCard title="推荐 · 卡片流" cnt="30 人" srcLabel="服务端排名" srcRows={[["来源", "严格硬闸"]]}>
        <div>名单正文</div>
      </ModuleCard>,
    );

    expect(screen.getByRole("heading", { name: "推荐 · 卡片流" })).toHaveClass("text-[14.5px]", "leading-5");
    expect(screen.getByText("30 人")).toHaveClass("text-[10.5px]");
    expect(container.querySelector('[data-vkpi-density="readable-module-header"]')).toHaveClass("min-h-11");
    expect(screen.getByText("名单正文")).not.toHaveClass("text-[14.5px]");
  });
});
