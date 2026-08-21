import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { ModuleCard } from "./MarketVoicePage.modules";

describe("shared cockpit module header density", () => {
  it("keeps module labels and page-scoped body copy readable without changing unrelated boards", () => {
    const { container } = render(
      <>
        <ModuleCard title="推荐 · 卡片流" cnt="30 人" srcLabel="服务端排名" srcRows={[["来源", "严格硬闸"]]} readableBody>
          <div>名单正文</div>
        </ModuleCard>
        <ModuleCard title="其他板块" srcLabel="既有密度" srcRows={[]}>
          <div>其他正文</div>
        </ModuleCard>
      </>,
    );

    expect(screen.getByRole("heading", { name: "推荐 · 卡片流" })).toHaveClass("text-[14.5px]", "leading-5");
    expect(screen.getByText("30 人")).toHaveClass("text-[10.5px]");
    expect(container.querySelector('[data-vkpi-density="readable-module-header"]')).toHaveClass("min-h-11");
    expect(container.querySelector('[data-vkpi-density="readable-module-body"]')).toHaveClass("text-[12.5px]", "leading-[1.55]");
    expect(screen.getByText("名单正文")).not.toHaveClass("text-[14.5px]");
    expect(screen.getByText("其他正文").parentElement).not.toHaveAttribute("data-vkpi-density");
  });
});
