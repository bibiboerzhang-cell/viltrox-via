import React from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MetricCard } from "./MetricCard";

afterEach(cleanup);

function metricItem(scopeData: Record<string, unknown>, id = "exposure", format = "number") {
  return {
    id,
    label: id,
    format,
    data: {
      all: scopeData,
      kol: scopeData,
      company: scopeData,
    },
  };
}

function renderCard(scopeData: Record<string, unknown>, id?: string, format?: string) {
  return render(
    <MetricCard
      item={metricItem(scopeData, id, format)}
      i={0}
      scope="company"
      onClick={() => {}}
    />,
  );
}

describe("MetricCard 真实趋势", () => {
  it("优先使用后端 delta_pct，并渲染 sparkline 与末端点", () => {
    const { container } = renderCard({
      value: 20,
      source: "owned_matrix",
      sourceLabel: "实时",
      color: "#06b6d4",
      spark: [10, 15, 20],
      deltaPct: 12.5,
    });

    expect(container.querySelector(".ds-kpi__spark")).not.toBeNull();
    expect(container.querySelector(".ds-sparkline__endpoint")).not.toBeNull();
    const badge = container.querySelector(".ds-kpi__delta");
    expect(badge?.textContent).toBe("▲12.5%");
    expect(badge?.classList.contains("ds-kpi__delta--up")).toBe(true);
  });

  it("后端 delta_pct 缺失时由首末点推导，0% 使用 flat", () => {
    const derived = renderCard({
      value: 75,
      source: "owned_matrix",
      color: "#06b6d4",
      spark: [100, 75],
      deltaPct: null,
    });
    expect(derived.container.querySelector(".ds-kpi__delta")?.textContent).toBe("▼25.0%");
    expect(derived.container.querySelector(".ds-kpi__delta--down")).not.toBeNull();
    cleanup();

    const flat = renderCard({
      value: 100,
      source: "owned_matrix",
      color: "#06b6d4",
      spark: [90, 100],
      deltaPct: 0,
    });
    expect(flat.container.querySelector(".ds-kpi__delta")?.textContent).toBe("0.0%");
    expect(flat.container.querySelector(".ds-kpi__delta--flat")).not.toBeNull();
  });

  it.each([
    ["gmv", "money"],
    ["roi", "multiplier"],
  ])("%s 无 series 时保持 -- 并渲染诚实空态", (id, format) => {
    const { container } = renderCard({
      value: null,
      source: "pending",
      color: "#fbbf24",
      spark: null,
      deltaPct: null,
      waiting: "待接入",
    }, id, format);

    expect(container.querySelector(".ds-kpi__val")?.textContent).toBe("--");
    expect(container.querySelector(".ds-kpi__series-empty")).not.toBeNull();
    expect(container.querySelector(".ds-kpi__signal-rail")).toBeNull();
    expect(container.querySelector(".ds-kpi__spark")).toBeNull();
    expect(container.querySelector(".ds-sparkline__endpoint")).toBeNull();
    expect(container.querySelector(".ds-kpi__delta")).toBeNull();
  });
});
