// 车道B TableShell:复用表格外壳 —— 加载骨架(>500ms 才显,防闪现)/ 行 stagger 入场一次 /
// 统一表头 · 空态 · 错误态。纯展示,零取数(取数逻辑留在各表本体,本壳只做壳)。
// 动效全部复用 motion.css 基元:SkeletonBlock 的 shimmer(加载)+ vk-row-enter(入场一次)+
// prefers-reduced-motion 全降级为静态(见 motion.css reduce 块);零新 npm 依赖。
// 沿用既有 vkpi-table / vkpi-table-wrap / vkpi-table-empty 类名,视觉与旧裸表一致。
import React from "react";
import { SkeletonBlock } from "./SkeletonBlock";
import { usePrefersReducedMotion } from "../AnimatedNumber";
import "./motion.css";

const e = React.createElement;

export interface TableShellColumn {
  /** 列 key:也用作 React key + renderCell 分派键。 */
  key: string;
  /** 表头内容。 */
  header: React.ReactNode;
  /** 可选:表头 + 单元格共用附加 className(如列宽/对齐)。 */
  className?: string;
  /** 可选:仅单元格(td)附加 className(不落到 th,如 vkpi-url-cell 省略号列)。 */
  cellClassName?: string;
}

export interface TableShellProps<T> {
  columns: TableShellColumn[];
  rows: T[];
  /** 加载态:仅当无既有数据时铺骨架(有数据时不闪,保留旧内容)。 */
  loading?: boolean;
  /** 错误态:非空且无数据时占满一行显示(与空态互斥)。 */
  error?: React.ReactNode;
  /** 空态文案(无数据 · 非加载 · 无错误)。 */
  emptyText?: React.ReactNode;
  /** 稳定行 key(memo + 入场一次的前提)。 */
  rowKey: (row: T, index: number) => React.Key;
  /** 单元格渲染:按列 key 分派,返回该格内容。 */
  renderCell: (row: T, column: TableShellColumn, rowIndex: number) => React.ReactNode;
  /** 骨架行数(默认 5)。 */
  skeletonRows?: number;
  /** 外壳/表格类名覆写(默认沿用 vkpi-table 体系)。 */
  wrapClassName?: string;
  tableClassName?: string;
}

// 延迟置真:active 持续超过 delayMs 才返回 true(<500ms 的加载不铺骨架 = 防闪现)。
function useDelayedFlag(active: boolean, delayMs: number): boolean {
  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    if (!active) {
      setOn(false);
      return undefined;
    }
    const t = globalThis.setTimeout(() => setOn(true), delayMs);
    return () => globalThis.clearTimeout(t);
  }, [active, delayMs]);
  return on;
}

type TableShellRowProps = {
  row: any;
  rowIndex: number;
  columns: TableShellColumn[];
  renderCell: (row: any, column: TableShellColumn, rowIndex: number) => React.ReactNode;
  reduced: boolean;
};

// 行组件 memo:轮询/滚动驱动的整表重渲时,payload(row 引用)未变则跳过本行重渲。
// 入场一次:vk-row-enter + 按行序 stagger(封顶 14 行,晚行齐现,避免长瀑布);
// reduced-motion 直接不挂 class(motion.css reduce 块另有 animation:none 双保险)。
const TableShellRow = React.memo(function TableShellRow(props: TableShellRowProps) {
  const { row, rowIndex, columns, renderCell, reduced } = props;
  const className = reduced ? undefined : "vk-row-enter";
  const style = reduced ? undefined : { animationDelay: `${Math.min(rowIndex, 14) * 24}ms` };
  return e(
    "tr",
    { className, style },
    columns.map((col) =>
      e("td", { key: col.key, className: col.cellClassName ?? col.className }, renderCell(row, col, rowIndex)),
    ),
  );
});

export function TableShell<T>(props: TableShellProps<T>) {
  const {
    columns,
    rows,
    loading = false,
    error,
    emptyText = "暂无数据",
    rowKey,
    renderCell,
    skeletonRows = 5,
    wrapClassName = "vkpi-table-wrap",
    tableClassName = "vkpi-table",
  } = props;
  const reduced = usePrefersReducedMotion();
  const colCount = columns.length;
  // 骨架仅在「加载中且无既有数据」时考虑,且需持续 >500ms 才真显(防闪现)。
  const showSkeleton = useDelayedFlag(Boolean(loading) && rows.length === 0, 500);

  let body: React.ReactNode;
  if (error != null && rows.length === 0) {
    body = e("tr", null, e("td", { className: "vkpi-table-empty", colSpan: colCount }, error));
  } else if (loading && rows.length === 0) {
    // <500ms 先留空 tbody(不跳版),超时才铺 shimmer 骨架行。
    body = showSkeleton
      ? Array.from({ length: Math.max(1, skeletonRows) }).map((_, r) =>
          e(
            "tr",
            { key: `sk-${r}`, "aria-hidden": true },
            columns.map((col) => e("td", { key: col.key, className: col.cellClassName ?? col.className }, e(SkeletonBlock, { className: "h-3 w-full rounded" }))),
          ),
        )
      : null;
  } else if (rows.length === 0) {
    body = e("tr", null, e("td", { className: "vkpi-table-empty", colSpan: colCount }, emptyText));
  } else {
    body = rows.map((row, i) =>
      e(TableShellRow, { key: rowKey(row, i), row, rowIndex: i, columns, renderCell, reduced }),
    );
  }

  return e(
    "div",
    { className: wrapClassName },
    e(
      "table",
      { className: tableClassName },
      e(
        "thead",
        null,
        e(
          "tr",
          null,
          columns.map((col) => e("th", { key: col.key, className: col.className }, col.header)),
        ),
      ),
      e("tbody", null, body),
    ),
  );
}

export default TableShell;
