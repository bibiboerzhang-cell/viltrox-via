import React from "react";
import ReactGridLayout, {
  type Layout,
  type LayoutItem as GridLayoutItem,
  type ResizeHandleAxis,
  verticalCompactor,
} from "react-grid-layout";
import { GripVertical, LayoutGrid, Plus, RotateCcw, Search, Sparkles, X } from "lucide-react";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import {
  DASHBOARD_LAYOUT_COLUMNS,
  DASHBOARD_LAYOUT_PREFERENCE,
  decodeDashboardLayoutPreference,
  encodeDashboardLayoutPreference,
  loadDashboardPreference,
  saveDashboardPreference,
} from "../dashboardPreferenceStore";
import "./EditableDashboardBoard.css";

const e = React.createElement;

// 「跨板块模块」= Dashboard 专用第四类(task #76 跨板块拉卡):palette 可拉入子板块
// 注册表模块(自带取数,来源徽可跳源板块)。板块页注册表仍只用前三类。
export type DashboardModuleCategory = "核心模块" | "实时模块" | "数据库模块" | "业务板块" | "跨板块模块";

export interface DashboardModuleDefinition {
  key: string;
  label: string;
  description: string;
  category: DashboardModuleCategory;
  defaultSpan: number;
  defaultHeight?: number;
  minSpan?: number;
  minHeight?: number;
  maxHeight?: number;
  render: () => React.ReactNode;
  allowMultiple?: boolean;
  /** Dashboard 跨页模块 palette 分组元数据；普通板块页可不提供。 */
  sourceBoard?: string;
  sourceLabel?: string;
}

export interface DashboardLayoutItem {
  instanceId: string;
  moduleKey: string;
  span: number;
  height?: number;
  x: number;
  y: number;
}

interface EditableDashboardBoardProps {
  modules: DashboardModuleDefinition[];
  defaultLayout: Array<Pick<DashboardLayoutItem, "moduleKey" | "span"> & Partial<Pick<DashboardLayoutItem, "height" | "x" | "y">>>;
  editing: boolean;
  storageKey?: string;
  /** Stable authenticated staff scope for the local-only fallback key. */
  localStorageScope?: string | number;
  /** One-time migration sources. Existing geometry is retained and the old value is not overwritten. */
  legacyStorageKeys?: string[];
  /** Safety modules appended below a migrated legacy layout without moving existing items. */
  requiredDefaultModuleKeys?: string[];
  apiToken?: string;
}

const CATEGORY_ORDER: DashboardModuleCategory[] = ["核心模块", "数据库模块", "实时模块", "业务板块", "跨板块模块"];
const FLOW_MODULES = new Set(["trend"]);
const BOARD_GAP_PX = 14;
const BOARD_ROW_HEIGHT_PX = 22;
const MIN_CARD_INLINE_PX = 220;
const DEFAULT_CARD_HEIGHT = 9;
const MEDIUM_COLUMNS = 6;
const NARROW_COLUMNS = 1;
const DESKTOP_MIN_INLINE_PX = 960;
const MEDIUM_MIN_INLINE_PX = 640;
const RESIZE_HANDLES: ResizeHandleAxis[] = ["n", "e", "s", "w", "ne", "se", "sw", "nw"];

function buildFlowPath(phase: number, amplitude = 1) {
  const points: string[] = [];
  for (let x = 0; x <= 1600; x += 16) {
    const y = 62
      + Math.sin((x / 200) * Math.PI * 2 + phase) * 20 * amplitude
      + Math.sin((x / 100) * Math.PI * 2) * 8 * amplitude;
    points.push(`${x},${y.toFixed(1)}`);
  }
  return `M${points.join(" L")}`;
}

const FLOW_PATH_A = buildFlowPath(0, 1);
const FLOW_PATH_B = buildFlowPath(1.7, 0.82);

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function normalizeSpan(value: unknown, fallback = 4, columns = DASHBOARD_LAYOUT_COLUMNS) {
  const span = Number(value);
  return Number.isFinite(span)
    ? clamp(Math.round(span), 1, columns)
    : clamp(fallback, 1, columns);
}

function normalizeHeight(value: unknown, fallback = DEFAULT_CARD_HEIGHT) {
  const height = Number(value);
  return Number.isFinite(height)
    ? clamp(Math.round(height), 3, 48)
    : clamp(fallback, 3, 48);
}

function normalizeModuleSpan(
  value: unknown,
  definition: DashboardModuleDefinition | undefined,
  fallback = 4,
) {
  const minimum = normalizeSpan(definition?.minSpan ?? 3, 3);
  return clamp(normalizeSpan(value, fallback), minimum, DASHBOARD_LAYOUT_COLUMNS);
}

function normalizeModuleHeight(
  value: unknown,
  definition: DashboardModuleDefinition | undefined,
  fallback = DEFAULT_CARD_HEIGHT,
) {
  const minimum = normalizeHeight(definition?.minHeight ?? 4, 4);
  const maximum = normalizeHeight(definition?.maxHeight ?? 36, 36);
  return clamp(
    normalizeHeight(value, definition?.defaultHeight ?? fallback),
    Math.min(minimum, maximum),
    Math.max(minimum, maximum),
  );
}

function finiteCoordinate(value: unknown) {
  const coordinate = Number(value);
  return Number.isInteger(coordinate) && coordinate >= 0 ? coordinate : null;
}

function layoutsMatch(left: DashboardLayoutItem[], right: DashboardLayoutItem[]) {
  return left.length === right.length && left.every((item, index) => {
    const other = right[index];
    return item.instanceId === other.instanceId
      && item.moduleKey === other.moduleKey
      && item.span === other.span
      && normalizeHeight(item.height) === normalizeHeight(other.height)
      && item.x === other.x
      && item.y === other.y;
  });
}

function rectanglesOverlap(
  left: Pick<DashboardLayoutItem, "x" | "y" | "span" | "height">,
  right: Pick<DashboardLayoutItem, "x" | "y" | "span" | "height">,
) {
  const leftHeight = normalizeHeight(left.height, 1);
  const rightHeight = normalizeHeight(right.height, 1);
  return left.x < right.x + right.span
    && left.x + left.span > right.x
    && left.y < right.y + rightHeight
    && left.y + leftHeight > right.y;
}

function packDashboardLayoutForColumns(items: DashboardLayoutItem[], columns: number) {
  const placed: DashboardLayoutItem[] = [];
  items.forEach((rawItem) => {
    const item = {
      ...rawItem,
      span: normalizeSpan(rawItem.span, 4, columns),
      height: normalizeHeight(rawItem.height, 1),
    };
    let resolved: DashboardLayoutItem | null = null;
    const maxSearchRows = Math.max(64, placed.reduce((bottom, row) => (
      Math.max(bottom, row.y + normalizeHeight(row.height, 1))
    ), 0) + item.height + 1);

    for (let y = 0; y <= maxSearchRows && !resolved; y += 1) {
      for (let x = 0; x <= columns - item.span; x += 1) {
        const candidate = { ...item, x, y };
        if (!placed.some((row) => rectanglesOverlap(candidate, row))) {
          resolved = candidate;
          break;
        }
      }
    }
    placed.push(resolved || { ...item, x: 0, y: maxSearchRows });
  });

  return placed.sort((left, right) => left.y - right.y || left.x - right.x);
}

export function compactDashboardLayout(items: DashboardLayoutItem[]): DashboardLayoutItem[] {
  return packDashboardLayoutForColumns(items, DASHBOARD_LAYOUT_COLUMNS);
}

function compactExistingGeometry(items: DashboardLayoutItem[]) {
  const itemMap = new Map(items.map((item) => [item.instanceId, item]));
  const compacted = verticalCompactor.compact(items.map((item) => ({
    i: item.instanceId,
    x: clamp(item.x, 0, Math.max(0, DASHBOARD_LAYOUT_COLUMNS - item.span)),
    y: Math.max(0, item.y),
    w: item.span,
    h: normalizeHeight(item.height),
  })), DASHBOARD_LAYOUT_COLUMNS);

  return compacted.map((gridItem) => {
    const source = itemMap.get(gridItem.i) as DashboardLayoutItem;
    return {
      ...source,
      x: gridItem.x,
      y: gridItem.y,
      span: gridItem.w,
      height: gridItem.h,
    };
  }).sort((left, right) => left.y - right.y || left.x - right.x);
}

export function dashboardColumnsForWidth(width: number) {
  if (!Number.isFinite(width) || width <= 0) return DASHBOARD_LAYOUT_COLUMNS;
  if (width < MEDIUM_MIN_INLINE_PX) return NARROW_COLUMNS;
  if (width < DESKTOP_MIN_INLINE_PX) return MEDIUM_COLUMNS;
  return DASHBOARD_LAYOUT_COLUMNS;
}

export function responsiveDashboardSpan(
  desktopSpan: number,
  columns: number,
  boardWidth: number,
) {
  if (columns <= NARROW_COLUMNS) return NARROW_COLUMNS;

  const proportionalSpan = clamp(
    Math.round((normalizeSpan(desktopSpan) / DASHBOARD_LAYOUT_COLUMNS) * columns),
    1,
    columns,
  );
  if (!Number.isFinite(boardWidth) || boardWidth <= 0) return proportionalSpan;

  const columnPitch = (boardWidth + BOARD_GAP_PX) / columns;
  const minimumReadableSpan = clamp(
    Math.ceil((MIN_CARD_INLINE_PX + BOARD_GAP_PX) / columnPitch),
    1,
    columns,
  );
  return Math.max(proportionalSpan, minimumReadableSpan);
}

function responsiveDashboardHeight(height: unknown, columns: number) {
  const desktopHeight = normalizeHeight(height);
  if (columns === DASHBOARD_LAYOUT_COLUMNS) return desktopHeight;
  if (columns === MEDIUM_COLUMNS) return Math.ceil(desktopHeight * 1.12);
  return Math.ceil(desktopHeight * 1.35);
}

function placeResponsiveLayout(
  items: DashboardLayoutItem[],
  columns: number,
  boardWidth: number,
) {
  if (columns === DASHBOARD_LAYOUT_COLUMNS) return items;
  return packDashboardLayoutForColumns(items.map((item) => ({
    ...item,
    span: responsiveDashboardSpan(item.span, columns, boardWidth),
    height: responsiveDashboardHeight(item.height, columns),
    x: 0,
    y: 0,
  })), columns);
}

function freshDefaultLayout(
  defaultLayout: EditableDashboardBoardProps["defaultLayout"],
  moduleMap: Map<string, DashboardModuleDefinition>,
) {
  return compactDashboardLayout(defaultLayout.map((item, index) => {
    const definition = moduleMap.get(item.moduleKey);
    return {
      instanceId: `default-${item.moduleKey}-${index}`,
      moduleKey: item.moduleKey,
      span: normalizeModuleSpan(item.span, definition),
      height: normalizeModuleHeight(item.height, definition),
      x: item.x ?? 0,
      y: item.y ?? 0,
    };
  }));
}

function normalizeStoredLayout(
  raw: unknown,
  moduleMap: Map<string, DashboardModuleDefinition>,
  idPrefix: string,
): DashboardLayoutItem[] | null {
  const decoded = decodeDashboardLayoutPreference<Record<string, unknown>>(raw);
  if (decoded === null) return null;
  if (decoded.length === 0) return [];

  const usedIds = new Set<string>();
  const candidates = decoded.flatMap((value, sourceIndex) => {
    if (!value || typeof value !== "object") return [];
    const moduleKey = String(value.moduleKey ?? value.type ?? "");
    const definition = moduleMap.get(moduleKey);
    if (!definition) return [];

    const baseId = String(value.instanceId ?? value.id ?? value.i ?? `${idPrefix}-${moduleKey}-${sourceIndex}`);
    let instanceId = baseId;
    let duplicate = 1;
    while (usedIds.has(instanceId)) {
      instanceId = `${baseId}-${sourceIndex}-${duplicate}`;
      duplicate += 1;
    }
    usedIds.add(instanceId);

    const storedX = finiteCoordinate(value.x ?? value.col);
    const storedY = finiteCoordinate(value.y ?? value.row);
    const storedHeight = finiteCoordinate(value.height ?? value.h);
    return [{
      item: {
        instanceId,
        moduleKey,
        span: normalizeModuleSpan(value.span ?? value.w ?? value.width, definition),
        height: normalizeModuleHeight(storedHeight ?? definition.defaultHeight, definition),
        x: storedX ?? 0,
        y: storedY ?? 0,
      },
      sourceIndex,
      hasGeometry: storedX !== null && storedY !== null && storedHeight !== null,
    }];
  });

  if (candidates.length === 0) return null;
  if (candidates.every(({ hasGeometry }) => hasGeometry)) {
    return compactExistingGeometry(candidates.map(({ item }) => item));
  }
  candidates.sort((left, right) => left.sourceIndex - right.sourceIndex);
  return compactDashboardLayout(candidates.map(({ item }) => item));
}

function loadLayout(
  storageKey: string,
  defaultLayout: EditableDashboardBoardProps["defaultLayout"],
  moduleMap: Map<string, DashboardModuleDefinition>,
  legacyStorageKeys: string[] = [],
  requiredDefaultModuleKeys: string[] = [],
) {
  if (typeof window === "undefined") return freshDefaultLayout(defaultLayout, moduleMap);
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey) || "null");
    const restored = normalizeStoredLayout(parsed, moduleMap, "restored");
    if (restored !== null) return restored;
  } catch {
    // Try a reviewed legacy key before falling back to the new default.
  }
  for (const legacyStorageKey of legacyStorageKeys) {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(legacyStorageKey) || "null");
      const legacy = normalizeStoredLayout(parsed, moduleMap, "migrated");
      if (legacy !== null) {
        const activeKeys = new Set(legacy.map((item) => item.moduleKey));
        let nextY = legacy.reduce((bottom, item) => Math.max(bottom, item.y + normalizeHeight(item.height)), 0);
        const additions = requiredDefaultModuleKeys.flatMap((moduleKey, index) => {
          if (activeKeys.has(moduleKey)) return [];
          const definition = moduleMap.get(moduleKey);
          const defaultItem = defaultLayout.find((item) => item.moduleKey === moduleKey);
          if (!definition || !defaultItem) return [];
          const height = normalizeModuleHeight(defaultItem.height, definition);
          const item: DashboardLayoutItem = {
            instanceId: `migrated-required-${moduleKey}-${index}`,
            moduleKey,
            span: normalizeModuleSpan(defaultItem.span, definition),
            height,
            x: 0,
            y: nextY,
          };
          nextY += height;
          activeKeys.add(moduleKey);
          return [item];
        });
        return [...legacy, ...additions];
      }
    } catch {
      // Continue to the next legacy key; malformed legacy data stays untouched.
    }
  }
  return freshDefaultLayout(defaultLayout, moduleMap);
}

function scopedLocalStorageKey(storageKey: string, scope: string | number | undefined) {
  const normalized = String(scope ?? "").trim();
  return normalized ? `${storageKey}:${encodeURIComponent(normalized)}` : storageKey;
}

function toGridLayout(
  items: DashboardLayoutItem[],
  moduleMap: Map<string, DashboardModuleDefinition>,
  columns: number,
  editing: boolean,
): Layout {
  return items.map((item): GridLayoutItem => {
    const definition = moduleMap.get(item.moduleKey);
    const desktopMinWidth = normalizeModuleSpan(definition?.minSpan ?? 3, definition, 3);
    const minimumWidth = columns === DASHBOARD_LAYOUT_COLUMNS
      ? desktopMinWidth
      : responsiveDashboardSpan(desktopMinWidth, columns, 0);
    const minimumHeight = columns === DASHBOARD_LAYOUT_COLUMNS
      ? normalizeModuleHeight(definition?.minHeight ?? 4, definition, 4)
      : 4;
    return {
      i: item.instanceId,
      x: item.x,
      y: item.y,
      w: item.span,
      h: normalizeHeight(item.height),
      minW: Math.min(item.span, minimumWidth),
      minH: Math.min(normalizeHeight(item.height), minimumHeight),
      maxW: columns,
      maxH: definition?.maxHeight ?? 36,
      isDraggable: editing,
      isResizable: editing,
      resizeHandles: RESIZE_HANDLES,
    };
  });
}

function fromGridLayout(
  gridLayout: Layout,
  current: DashboardLayoutItem[],
  moduleMap: Map<string, DashboardModuleDefinition>,
) {
  const currentMap = new Map(current.map((item) => [item.instanceId, item]));
  return gridLayout.flatMap((gridItem) => {
    const source = currentMap.get(gridItem.i);
    if (!source) return [];
    const definition = moduleMap.get(source.moduleKey);
    return [{
      ...source,
      span: normalizeModuleSpan(gridItem.w, definition, source.span),
      height: normalizeModuleHeight(gridItem.h, definition, normalizeHeight(source.height)),
      x: clamp(gridItem.x, 0, Math.max(0, DASHBOARD_LAYOUT_COLUMNS - gridItem.w)),
      y: Math.max(0, gridItem.y),
    }];
  }).sort((left, right) => left.y - right.y || left.x - right.x);
}

function ModuleFlowField() {
  return e("div", { className: "vkpi-board-module__flow", "aria-hidden": "true" },
    e("svg", { className: "is-primary", viewBox: "0 0 1600 120", preserveAspectRatio: "none", focusable: "false" },
      e("path", { d: FLOW_PATH_A, vectorEffect: "non-scaling-stroke" }),
    ),
    e("svg", { className: "is-secondary", viewBox: "0 0 1600 120", preserveAspectRatio: "none", focusable: "false" },
      e("path", { d: FLOW_PATH_B, vectorEffect: "non-scaling-stroke" }),
    ),
  );
}

function GridResizeHandle(axis: ResizeHandleAxis, ref: React.Ref<HTMLElement>) {
  return e("span", {
    ref,
    className: `react-resizable-handle react-resizable-handle-${axis} vkpi-grid-resize-handle`,
    "data-resize-axis": axis,
    "aria-hidden": "true",
  });
}

export function EditableDashboardBoard({
  modules,
  defaultLayout,
  editing,
  storageKey = "vkpi-dashboard-layout-v1",
  localStorageScope,
  legacyStorageKeys = [],
  requiredDefaultModuleKeys = [],
  apiToken = "",
}: EditableDashboardBoardProps) {
  const resolvedStorageKey = scopedLocalStorageKey(storageKey, localStorageScope);
  const resolvedLegacyStorageKeys = legacyStorageKeys.map((key) => scopedLocalStorageKey(key, localStorageScope));
  const moduleMap = React.useMemo(() => new Map(modules.map((module) => [module.key, module])), [modules]);
  const moduleKeySignature = modules.map((module) => module.key).join("|");
  const [layout, setLayout] = React.useState<DashboardLayoutItem[]>(() => loadLayout(
    resolvedStorageKey,
    defaultLayout,
    moduleMap,
    resolvedLegacyStorageKeys,
    requiredDefaultModuleKeys,
  ));
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [paletteQuery, setPaletteQuery] = React.useState("");
  const [boardMetrics, setBoardMetrics] = React.useState({ width: 0, columns: DASHBOARD_LAYOUT_COLUMNS });
  const [syncState, setSyncState] = React.useState<"local" | "loading" | "saving" | "saved" | "error">(apiToken ? "loading" : "local");
  const boardRef = React.useRef<HTMLDivElement | null>(null);
  const layoutRef = React.useRef(layout);

  React.useEffect(() => {
    layoutRef.current = layout;
  }, [layout]);

  React.useEffect(() => {
    if (boardRef.current) {
      boardRef.current.dataset.dashboardColumns = String(boardMetrics.columns);
    }
  }, [boardMetrics.columns]);

  React.useEffect(() => {
    try {
      window.localStorage.setItem(resolvedStorageKey, JSON.stringify(encodeDashboardLayoutPreference(layout)));
    } catch {
      // The board remains usable when storage is disabled.
    }
  }, [layout, resolvedStorageKey]);

  React.useEffect(() => {
    const board = boardRef.current;
    if (!board) return undefined;
    const measure = (observedWidth?: number) => {
      const width = observedWidth && observedWidth > 0
        ? observedWidth
        : board.getBoundingClientRect().width || board.clientWidth;
      if (!width) return;
      const roundedWidth = Math.round(width * 10) / 10;
      const columns = dashboardColumnsForWidth(roundedWidth);
      setBoardMetrics((current) => current.width === roundedWidth && current.columns === columns
        ? current
        : { width: roundedWidth, columns });
    };

    measure();
    const observer = typeof ResizeObserver === "function"
      ? new ResizeObserver((entries) => measure(entries[0]?.contentRect.width))
      : null;
    const onWindowResize = () => measure();
    observer?.observe(board);
    window.addEventListener("resize", onWindowResize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", onWindowResize);
    };
  }, []);

  React.useEffect(() => {
    if (!apiToken) {
      setSyncState("local");
      return;
    }
    let alive = true;
    setSyncState("loading");
    loadDashboardPreference<unknown>(apiToken, DASHBOARD_LAYOUT_PREFERENCE)
      .then((remoteLayout) => {
        if (!alive) return;
        const normalized = normalizeStoredLayout(remoteLayout, moduleMap, "remote");
        if (normalized !== null) {
          layoutRef.current = normalized;
          setLayout(normalized);
        }
        setSyncState("saved");
      })
      .catch(() => {
        if (alive) setSyncState("error");
      });
    return () => { alive = false; };
  }, [apiToken, moduleKeySignature]);

  React.useEffect(() => {
    if (!paletteOpen) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen]);

  const persistLayout = React.useCallback((next: DashboardLayoutItem[]) => {
    if (!apiToken) return;
    setSyncState("saving");
    saveDashboardPreference(apiToken, DASHBOARD_LAYOUT_PREFERENCE, encodeDashboardLayoutPreference(next))
      .then(() => setSyncState("saved"))
      .catch(() => setSyncState("error"));
  }, [apiToken]);

  const replaceLayout = React.useCallback((next: DashboardLayoutItem[], persist = true) => {
    if (layoutsMatch(layoutRef.current, next)) return;
    layoutRef.current = next;
    setLayout(next);
    if (persist) persistLayout(next);
  }, [persistLayout]);

  const restoreDefault = React.useCallback(() => {
    replaceLayout(freshDefaultLayout(defaultLayout, moduleMap));
  }, [defaultLayout, moduleMap, replaceLayout]);

  const autoArrange = React.useCallback(() => {
    replaceLayout(compactDashboardLayout(layoutRef.current));
  }, [replaceLayout]);

  const removeItem = React.useCallback((instanceId: string) => {
    replaceLayout(compactExistingGeometry(layoutRef.current.filter((item) => item.instanceId !== instanceId)));
  }, [replaceLayout]);

  const addModule = React.useCallback((module: DashboardModuleDefinition) => {
    const next = compactDashboardLayout([
      ...layoutRef.current,
      {
        instanceId: `${module.key}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        moduleKey: module.key,
        span: normalizeModuleSpan(module.defaultSpan, module),
        height: normalizeModuleHeight(module.defaultHeight, module),
        x: 0,
        y: 0,
      },
    ]);
    replaceLayout(next);
    setPaletteOpen(false);
  }, [replaceLayout]);

  const responsiveLayout = React.useMemo(() => placeResponsiveLayout(
    layout,
    boardMetrics.columns,
    boardMetrics.width,
  ), [boardMetrics, layout]);
  const canEditGeometry = editing && boardMetrics.columns === DASHBOARD_LAYOUT_COLUMNS;
  const gridLayout = React.useMemo(() => toGridLayout(
    responsiveLayout,
    moduleMap,
    boardMetrics.columns,
    canEditGeometry,
  ), [boardMetrics.columns, canEditGeometry, moduleMap, responsiveLayout]);
  const activeModuleKeys = React.useMemo(() => new Set(layout.map((item) => item.moduleKey)), [layout]);
  const boardSizeClass = boardMetrics.columns === NARROW_COLUMNS
    ? "is-narrow"
    : boardMetrics.columns === MEDIUM_COLUMNS
      ? "is-medium"
      : "is-desktop";
  const syncLabel = syncState === "loading"
    ? "读取账户布局"
    : syncState === "saving"
      ? "正在保存"
      : syncState === "saved"
        ? "账户偏好已保存"
        : syncState === "error"
          ? "后端暂不可用，本机已保存"
          : "本机已保存";

  const applyGridLayout = React.useCallback((nextGridLayout: Layout, persist: boolean) => {
    if (!canEditGeometry) return;
    const next = fromGridLayout(nextGridLayout, layoutRef.current, moduleMap);
    replaceLayout(next, persist);
  }, [canEditGeometry, moduleMap, replaceLayout]);

  const normalizedPaletteQuery = paletteQuery.trim().toLowerCase();
  const visiblePaletteModules = normalizedPaletteQuery
    ? modules.filter((module) => [module.label, module.description, module.sourceLabel]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(normalizedPaletteQuery))
    : modules;
  const renderPaletteButton = (module: DashboardModuleDefinition) => {
    const alreadyAdded = activeModuleKeys.has(module.key) && !module.allowMultiple;
    return e("button", {
      key: module.key,
      type: "button",
      disabled: alreadyAdded,
      onClick: () => addModule(module),
      className: alreadyAdded ? "is-added" : "",
      title: alreadyAdded ? `${module.label} 已在看板` : `添加 ${module.label} 到看板`,
    },
      e("strong", null, module.label),
      e("span", null, module.description),
      e("small", null, alreadyAdded
        ? "已在看板"
        : `默认 ${module.defaultSpan}/12 · 高 ${module.defaultHeight ?? DEFAULT_CARD_HEIGHT} 格`),
    );
  };

  return e(React.Fragment, null,
    editing && e("div", { className: "vkpi-board-edit-hint", role: "status" },
      e("span", null,
        e("strong", null, "编辑模式"),
        canEditGeometry
          ? " · 拖动顶部手柄磁吸换位，拖动边缘或四角自由调整宽高"
          : " · 当前宽度自动紧凑排布；回到宽屏后可拖动和缩放",
        e("small", { className: `vkpi-board-edit-hint__sync is-${syncState}` }, syncLabel),
      ),
      e("div", { className: "vkpi-board-edit-hint__actions" },
        e("button", { type: "button", onClick: autoArrange, title: "消除空洞并向上磁吸" },
          e(Sparkles, { size: 13 }),
          "自动排列",
        ),
        e("button", { type: "button", onClick: restoreDefault, title: "恢复 Dashboard 默认模块和位置" },
          e(RotateCcw, { size: 13 }),
          "恢复默认",
        ),
      ),
    ),
    e(ReactGridLayout, {
      children: null,
      innerRef: boardRef,
      className: `vkpi-editable-board ${boardSizeClass} ${editing ? "is-editing" : ""}`,
      width: boardMetrics.width || 1200,
      layout: gridLayout,
      gridConfig: {
        cols: boardMetrics.columns,
        rowHeight: BOARD_ROW_HEIGHT_PX,
        margin: [BOARD_GAP_PX, BOARD_GAP_PX],
        containerPadding: [0, 0],
      },
      dragConfig: {
        enabled: canEditGeometry,
        bounded: true,
        handle: ".vkpi-board-module__drag-handle",
        cancel: "button:not(.vkpi-board-module__drag-handle),a,input,textarea,select,video",
        threshold: 4,
      },
      resizeConfig: {
        enabled: canEditGeometry,
        handles: RESIZE_HANDLES,
        handleComponent: GridResizeHandle,
      },
      compactor: verticalCompactor,
      onLayoutChange: (next: Layout) => applyGridLayout(next, false),
      onDragStop: (next: Layout) => applyGridLayout(next, true),
      onResizeStop: (next: Layout) => applyGridLayout(next, true),
    },
      responsiveLayout.map((item) => {
        const definition = moduleMap.get(item.moduleKey);
        if (!definition) return null;
        return e("section", {
          key: item.instanceId,
          className: `vkpi-board-module vkpi-board-module--${definition.key}`,
          "data-dashboard-instance": item.instanceId,
          "data-dashboard-x": item.x,
          "data-dashboard-y": item.y,
          "data-dashboard-span": item.span,
          "data-dashboard-height": normalizeHeight(item.height),
          style: {
            "--vkpi-module-span": item.span,
            "--vkpi-module-height": normalizeHeight(item.height),
          } as React.CSSProperties,
        },
          FLOW_MODULES.has(definition.key) && e(ModuleFlowField),
          editing && e("div", { className: "vkpi-board-module__tools", "aria-label": `${definition.label} 布局工具` },
            e("button", {
              type: "button",
              className: "vkpi-board-module__drag-handle",
              disabled: !canEditGeometry,
              title: canEditGeometry ? "拖动磁吸换位" : "宽屏下可拖动",
              "aria-label": `拖动 ${definition.label}`,
            }, e(GripVertical, { size: 14 })),
            e("button", { type: "button", onClick: () => removeItem(item.instanceId), title: "移除模块", "aria-label": `移除 ${definition.label}` }, e(X, { size: 13 })),
          ),
          e("div", { className: "vkpi-board-module__content" }, definition.render()),
        );
      }),
    ),
    editing && e("button", {
      type: "button",
      className: "vkpi-board-add",
      onClick: () => setPaletteOpen(true),
      title: "打开模块库并添加 Dashboard 模块",
    },
      e(Plus, { size: 17 }),
      e("span", null, "添加模块"),
    ),
    paletteOpen && e("div", {
      className: "vkpi-module-palette",
      role: "presentation",
      onMouseDown: (event: React.MouseEvent<HTMLDivElement>) => {
        if (event.target === event.currentTarget) setPaletteOpen(false);
      },
    },
      e("div", { className: "vkpi-module-palette__dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "vkpi-module-palette-title" },
        e("div", { className: "vkpi-module-palette__head" },
          e("div", null,
            e("div", { className: "vkpi-module-palette__eyebrow" }, e(LayoutGrid, { size: 13 }), "Dashboard Modules"),
            e("h2", { id: "vkpi-module-palette-title" }, "添加模块"),
            e("p", null, "所有模块都绑定现有页面、组件或真实接口；无数据时保持诚实空态。"),
          ),
          e("button", { type: "button", onClick: () => setPaletteOpen(false), title: "关闭模块库", "aria-label": "关闭添加模块" }, e(X, { size: 16 })),
        ),
        e("label", { className: "vkpi-module-palette__search" },
          e(Search, { size: 14 }),
          e("input", {
            value: paletteQuery,
            onChange: (event: React.ChangeEvent<HTMLInputElement>) => setPaletteQuery(event.target.value),
            placeholder: "搜索模块或来源页面，如“历史”“Projects”",
            "aria-label": "搜索 Dashboard 模块",
            autoFocus: true,
          }),
          paletteQuery && e("button", { type: "button", onClick: () => setPaletteQuery(""), "aria-label": "清空模块搜索" }, e(X, { size: 13 })),
        ),
        e("div", { className: "vkpi-module-palette__body" },
          CATEGORY_ORDER.map((category) => {
            const categoryModules = visiblePaletteModules.filter((module) => module.category === category);
            if (categoryModules.length === 0) return null;
            const sourceGroups = category === "跨板块模块"
              ? Array.from(categoryModules.reduce((groups, module) => {
                const label = module.sourceLabel || "其他来源";
                const group = groups.get(label) || [];
                group.push(module);
                groups.set(label, group);
                return groups;
              }, new Map<string, DashboardModuleDefinition[]>()).entries())
              : [[category, categoryModules] as [string, DashboardModuleDefinition[]]];
            return e("section", { key: category, className: "vkpi-module-palette__section" },
              e("h3", null, category, e("span", null, categoryModules.length)),
              sourceGroups.map(([sourceLabel, sourceModules]) => e("div", { key: sourceLabel, className: "vkpi-module-palette__source" },
                category === "跨板块模块" && e("h4", null, sourceLabel, e("span", null, sourceModules.length)),
                e("div", { className: "vkpi-module-palette__grid" }, sourceModules.map(renderPaletteButton)),
              )),
            );
          }),
          visiblePaletteModules.length === 0 && e("div", { className: "vkpi-module-palette__empty" }, `没有匹配“${paletteQuery.trim()}”的模块`),
        ),
      ),
    ),
  );
}
