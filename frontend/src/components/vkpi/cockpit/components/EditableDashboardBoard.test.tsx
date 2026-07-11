import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { DASHBOARD_LAYOUT_SCHEMA_VERSION } from "../dashboardPreferenceStore";
import {
  compactDashboardLayout,
  EditableDashboardBoard,
  type DashboardLayoutItem,
  type DashboardModuleDefinition,
} from "./EditableDashboardBoard";

const STORAGE_KEY = "vkpi-dashboard-layout-test";

const modules: DashboardModuleDefinition[] = [
  { key: "alpha", label: "模块 A", description: "A", category: "核心模块", defaultSpan: 12, render: () => <div>模块 A 内容</div> },
  { key: "beta", label: "模块 B", description: "B", category: "实时模块", defaultSpan: 4, render: () => <div>模块 B 内容</div> },
  { key: "gamma", label: "模块 C", description: "C", category: "业务板块", defaultSpan: 6, render: () => <div>模块 C 内容</div> },
];

const defaultLayout = [
  { moduleKey: "alpha", span: 12 },
  { moduleKey: "beta", span: 4 },
];

beforeEach(() => window.localStorage.clear());

describe("EditableDashboardBoard", () => {
  it("用后续可容纳模块填补桌面行空位", () => {
    const item = (instanceId: string, span: number): DashboardLayoutItem => ({
      instanceId,
      moduleKey: instanceId,
      span,
      height: 3,
      x: 0,
      y: 0,
    });

    expect(compactDashboardLayout([item("a", 8), item("b", 8), item("c", 4)]))
      .toEqual([
        item("a", 8),
        { ...item("c", 4), x: 8 },
        { ...item("b", 8), y: 3 },
      ]);
  });

  it("显示八向缩放手柄，并可添加、移除和恢复默认布局", async () => {
    render(<EditableDashboardBoard modules={modules} defaultLayout={defaultLayout} editing storageKey={STORAGE_KEY} />);

    const alphaSection = screen.getByText("模块 A 内容").closest("section") as HTMLElement;
    expect(alphaSection?.style.getPropertyValue("--vkpi-module-span")).toBe("12");
    expect(alphaSection.querySelectorAll("[data-resize-axis]")).toHaveLength(8);
    expect(Array.from(alphaSection.querySelectorAll<HTMLElement>("[data-resize-axis]"))
      .map((handle) => handle.dataset.resizeAxis)).toEqual(["n", "e", "s", "w", "ne", "se", "sw", "nw"]);

    fireEvent.click(screen.getByRole("button", { name: "移除 模块 B" }));
    expect(screen.queryByText("模块 B 内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "添加模块" }));
    const palette = screen.getByRole("dialog", { name: "添加模块" });
    expect(palette).toBeInTheDocument();
    expect(within(palette).getByRole("button", { name: /模块 A/ })).toBeDisabled();
    fireEvent.click(within(palette).getByRole("button", { name: /模块 C/ }));
    expect(screen.getByText("模块 C 内容")).toBeInTheDocument();

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.version).toBe(DASHBOARD_LAYOUT_SCHEMA_VERSION);
      expect(saved.items.map((item: { moduleKey: string }) => item.moduleKey)).toEqual(["alpha", "gamma"]);
      expect(saved.items[0]).toMatchObject({ moduleKey: "alpha", span: 12, height: 9, x: 0, y: 0 });
    });

    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    expect(screen.getByText("模块 B 内容")).toBeInTheDocument();
    expect(screen.queryByText("模块 C 内容")).not.toBeInTheDocument();
    expect(alphaSection?.style.getPropertyValue("--vkpi-module-span")).toBe("12");
  });

  it("重新挂载时迁移旧数组布局并过滤未知模块", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([
      { id: "saved-c", type: "gamma", w: 6 },
      { instanceId: "unknown", moduleKey: "removed", span: 12 },
    ]));

    render(<EditableDashboardBoard modules={modules} defaultLayout={defaultLayout} editing={false} storageKey={STORAGE_KEY} />);

    expect(screen.getByText("模块 C 内容")).toBeInTheDocument();
    expect(screen.queryByText("模块 A 内容")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加模块" })).not.toBeInTheDocument();

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved).toMatchObject({
        version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
        columns: 12,
        items: [{ instanceId: "saved-c", moduleKey: "gamma", span: 6, height: 9, x: 0, y: 0 }],
      });
    });
  });

  it("加载旧偏好时修复低于模块可读下限的宽度", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([
      { instanceId: "saved-kpi", moduleKey: "alpha", span: 3 },
    ]));
    const modulesWithMinimum = modules.map((module) => module.key === "alpha"
      ? { ...module, minSpan: 12 }
      : module);

    render(<EditableDashboardBoard
      modules={modulesWithMinimum}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={STORAGE_KEY}
    />);

    const alpha = screen.getByText("模块 A 内容").closest("section") as HTMLElement;
    expect(alpha.dataset.dashboardSpan).toBe("12");
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.items[0]).toMatchObject({ moduleKey: "alpha", span: 12 });
    });
  });

  it("自动排列会消除二维空洞并保存紧凑几何", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
      columns: 12,
      items: [
        { instanceId: "saved-a", moduleKey: "alpha", span: 4, height: 5, x: 0, y: 8 },
        { instanceId: "saved-b", moduleKey: "beta", span: 4, height: 5, x: 4, y: 8 },
        { instanceId: "saved-c", moduleKey: "gamma", span: 4, height: 5, x: 8, y: 20 },
      ],
    }));
    render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={[
        { moduleKey: "alpha", span: 4 },
        { moduleKey: "beta", span: 4 },
        { moduleKey: "gamma", span: 4 },
      ]}
      editing
      storageKey={STORAGE_KEY}
    />);

    const board = document.querySelector(".vkpi-editable-board") as HTMLDivElement;
    fireEvent.click(screen.getByRole("button", { name: "自动排列" }));

    const order = Array.from(board.querySelectorAll<HTMLElement>("[data-dashboard-instance]"))
      .map((section) => section.dataset.dashboardInstance);
    expect(order).toEqual(["saved-a", "saved-b", "saved-c"]);
    expect(Array.from(board.querySelectorAll<HTMLElement>("[data-dashboard-instance]"))
      .map((section) => section.dataset.dashboardY)).toEqual(["0", "0", "0"]);

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.items.map((item: { moduleKey: string }) => item.moduleKey)).toEqual(["alpha", "beta", "gamma"]);
      expect(saved.items.map((item: { y: number }) => item.y)).toEqual([0, 0, 0]);
    });
  });

  it("按看板实际宽度在 12、6、1 列间自动回流", async () => {
    render(<EditableDashboardBoard modules={modules} defaultLayout={defaultLayout} editing storageKey={STORAGE_KEY} />);
    const board = document.querySelector(".vkpi-editable-board") as HTMLDivElement;
    let width = 800;
    Object.defineProperty(board, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ width, height: 800, top: 0, right: width, bottom: 800, left: 0, x: 0, y: 0, toJSON: () => ({}) }),
    });

    fireEvent.resize(window);
    await waitFor(() => expect(board.dataset.dashboardColumns).toBe("6"));
    const alpha = screen.getByText("模块 A 内容").closest("section") as HTMLElement;
    const beta = screen.getByText("模块 B 内容").closest("section") as HTMLElement;
    expect(alpha.dataset.dashboardSpan).toBe("6");
    expect(beta.dataset.dashboardSpan).toBe("2");

    width = 500;
    fireEvent.resize(window);
    await waitFor(() => expect(board.dataset.dashboardColumns).toBe("1"));
    expect(alpha.dataset.dashboardSpan).toBe("1");
    expect(beta.dataset.dashboardSpan).toBe("1");
    expect(alpha.dataset.dashboardX).toBe("0");
    expect(Number(beta.dataset.dashboardY)).toBeGreaterThan(0);
  });
});
