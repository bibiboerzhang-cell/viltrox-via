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
  it("按 staff 隔离 Dashboard 的 localStorage 降级布局", async () => {
    const first = render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={STORAGE_KEY}
      localStorageScope="staff-7"
    />);

    await waitFor(() => {
      expect(window.localStorage.getItem(`${STORAGE_KEY}:staff-7`)).not.toBeNull();
    });
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(window.localStorage.getItem(`${STORAGE_KEY}:staff-8`)).toBeNull();

    first.unmount();
    window.localStorage.setItem(`${STORAGE_KEY}:staff-8`, JSON.stringify([
      { instanceId: "staff-8-module", moduleKey: "gamma", span: 6 },
    ]));
    render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={STORAGE_KEY}
      localStorageScope="staff-8"
    />);

    expect(screen.getByText("模块 C 内容")).toBeInTheDocument();
    expect(screen.queryByText("模块 A 内容")).not.toBeInTheDocument();
  });

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
    expect(alphaSection).toHaveAttribute("data-dashboard-module", "alpha");
    expect(alphaSection?.style.getPropertyValue("--vkpi-module-span")).toBe("12");
    expect(alphaSection.querySelectorAll("[data-resize-axis]")).toHaveLength(8);
    expect(Array.from(alphaSection.querySelectorAll<HTMLElement>("[data-resize-axis]"))
      .map((handle) => handle.dataset.resizeAxis)).toEqual(["n", "e", "s", "w", "ne", "se", "sw", "nw"]);

    fireEvent.click(screen.getByRole("button", { name: "移除 模块 B" }));
    expect(screen.queryByText("模块 B 内容")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "添加模块" }));
    const palette = screen.getByRole("dialog", { name: "添加模块" });
    expect(palette).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "恢复默认" })).toHaveAttribute("title", expect.stringContaining("默认模块"));
    expect(screen.getByRole("button", { name: "添加模块" })).toHaveAttribute("title", expect.stringContaining("模块库"));
    expect(within(palette).getByRole("button", { name: /模块 A/ })).toBeDisabled();
    expect(within(palette).getByRole("button", { name: /模块 A/ })).toHaveAttribute("title", "模块 A 已在看板");
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

  it("全盘跨页模块按来源分组并支持搜索", () => {
    const fullModules: DashboardModuleDefinition[] = [
      ...modules,
      { key: "xb-projects-cost", label: "成本概览", description: "真实成本", category: "跨板块模块", sourceBoard: "projects", sourceLabel: "Projects", defaultSpan: 4, render: () => <div /> },
      { key: "xb-pool-history", label: "搜索历史", description: "最近会话", category: "跨板块模块", sourceBoard: "kol-pool", sourceLabel: "KOL Pool", defaultSpan: 8, render: () => <div /> },
    ];
    render(<EditableDashboardBoard modules={fullModules} defaultLayout={defaultLayout} editing storageKey={STORAGE_KEY} />);

    fireEvent.click(screen.getByRole("button", { name: "添加模块" }));
    const palette = screen.getByRole("dialog", { name: "添加模块" });
    expect(within(palette).getByRole("heading", { name: /项目/ })).toBeInTheDocument();
    expect(within(palette).getByRole("heading", { name: /KOL 人才库/ })).toBeInTheDocument();

    fireEvent.change(within(palette).getByRole("textbox", { name: "搜索 Dashboard 模块" }), { target: { value: "历史" } });
    expect(within(palette).getByRole("button", { name: /搜索历史/ })).toBeInTheDocument();
    expect(within(palette).queryByRole("button", { name: /成本概览/ })).not.toBeInTheDocument();
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

  it("从旧 storageKey 迁移时保留现有几何，只在底部补必需模块且不覆写旧值", async () => {
    const nextKey = `${STORAGE_KEY}-v2`;
    const legacyKey = `${STORAGE_KEY}-v1`;
    const legacyValue = JSON.stringify({
      version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
      columns: 12,
      items: [
        { instanceId: "legacy-a", moduleKey: "alpha", span: 8, height: 5, x: 0, y: 0 },
        { instanceId: "legacy-c", moduleKey: "gamma", span: 4, height: 5, x: 8, y: 0 },
      ],
    });
    window.localStorage.setItem(legacyKey, legacyValue);

    render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={nextKey}
      legacyStorageKeys={[legacyKey]}
      requiredDefaultModuleKeys={["beta"]}
    />);

    expect(screen.getByText("模块 A 内容")).toBeInTheDocument();
    expect(screen.getByText("模块 C 内容")).toBeInTheDocument();
    expect(screen.getByText("模块 B 内容")).toBeInTheDocument();
    expect(window.localStorage.getItem(legacyKey)).toBe(legacyValue);

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(nextKey) || "null");
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "alpha"))
        .toMatchObject({ instanceId: "legacy-a", span: 8, height: 5, x: 0, y: 0 });
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "gamma"))
        .toMatchObject({ instanceId: "legacy-c", span: 4, height: 5, x: 8, y: 0 });
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "beta"))
        .toMatchObject({ instanceId: "migrated-required-beta-0", x: 0, y: 5 });
    });
  });

  it("将当前 key 上的 v3 布局一次迁移到 v4 并在底部补必需模块", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      version: 3,
      columns: 12,
      items: [
        { instanceId: "v3-alpha", moduleKey: "alpha", span: 12, height: 5, x: 0, y: 0 },
      ],
    }));

    render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={STORAGE_KEY}
      requiredDefaultModuleKeys={["beta"]}
    />);

    expect(screen.getByText("模块 A 内容")).toBeInTheDocument();
    expect(screen.getByText("模块 B 内容")).toBeInTheDocument();
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.version).toBe(DASHBOARD_LAYOUT_SCHEMA_VERSION);
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "alpha"))
        .toMatchObject({ instanceId: "v3-alpha", x: 0, y: 0, height: 5 });
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "beta"))
        .toMatchObject({ instanceId: "restored-required-beta-0", x: 0, y: 5 });
    });
  });

  it("尊重 v4 用户删除，后续挂载不会把必需默认模块重新加回", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
      version: DASHBOARD_LAYOUT_SCHEMA_VERSION,
      columns: 12,
      items: [
        { instanceId: "v4-alpha", moduleKey: "alpha", span: 12, height: 5, x: 0, y: 0 },
      ],
    }));

    render(<EditableDashboardBoard
      modules={modules}
      defaultLayout={defaultLayout}
      editing={false}
      storageKey={STORAGE_KEY}
      requiredDefaultModuleKeys={["beta"]}
    />);

    expect(screen.getByText("模块 A 内容")).toBeInTheDocument();
    expect(screen.queryByText("模块 B 内容")).not.toBeInTheDocument();
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.version).toBe(DASHBOARD_LAYOUT_SCHEMA_VERSION);
      expect(saved.items.map((item: { moduleKey: string }) => item.moduleKey)).toEqual(["alpha"]);
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

  it("拖拽磁吸与自由缩放结束后保存二维几何", async () => {
    render(<EditableDashboardBoard modules={modules} defaultLayout={defaultLayout} editing storageKey={STORAGE_KEY} />);
    const board = document.querySelector(".vkpi-editable-board") as HTMLDivElement;
    const beta = screen.getByText("模块 B 内容").closest("section") as HTMLElement;

    Object.defineProperties(board, {
      clientHeight: { configurable: true, value: 2000 },
      getBoundingClientRect: {
        configurable: true,
        value: () => ({ width: 1200, height: 2000, top: 0, right: 1200, bottom: 2000, left: 0, x: 0, y: 0, toJSON: () => ({}) }),
      },
    });
    Object.defineProperties(beta, {
      offsetParent: { configurable: true, value: board },
      getBoundingClientRect: {
        configurable: true,
        value: () => ({ width: 390, height: 310, top: 324, right: 390, bottom: 634, left: 0, x: 0, y: 324, toJSON: () => ({}) }),
      },
    });
    fireEvent.resize(window);
    await waitFor(() => expect(board.dataset.dashboardColumns).toBe("12"));

    fireEvent.mouseDown(screen.getByRole("button", { name: "拖动 模块 B" }), { button: 0, clientX: 20, clientY: 340 });
    fireEvent.mouseMove(document, { buttons: 1, clientX: 420, clientY: 340 });
    fireEvent.mouseUp(document, { button: 0, clientX: 420, clientY: 340 });

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      expect(saved.items.find((item: { moduleKey: string }) => item.moduleKey === "beta").x).toBeGreaterThan(0);
    });

    const southeast = beta.querySelector<HTMLElement>('[data-resize-axis="se"]') as HTMLElement;
    fireEvent.mouseDown(southeast, { button: 0, clientX: 390, clientY: 634 });
    fireEvent.mouseMove(document, { buttons: 1, clientX: 650, clientY: 750 });
    fireEvent.mouseUp(document, { button: 0, clientX: 650, clientY: 750 });

    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      const savedBeta = saved.items.find((item: { moduleKey: string }) => item.moduleKey === "beta");
      expect(savedBeta.span).toBeGreaterThan(4);
      expect(savedBeta.height).toBeGreaterThan(9);
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

    const savedWhileNarrow = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    expect(savedWhileNarrow.items.map((item: { span: number }) => item.span)).toEqual([12, 4]);

    width = 1200;
    fireEvent.resize(window);
    await waitFor(() => expect(board.dataset.dashboardColumns).toBe("12"));
    expect(alpha.dataset.dashboardSpan).toBe("12");
    expect(beta.dataset.dashboardSpan).toBe("4");
  });
});
