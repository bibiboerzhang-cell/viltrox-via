/**
 * DataTable — reusable sortable table for admin lists
 *
 * Columns are defined with cell renderers. Sorting is external (caller owns state).
 * The table is "dumb" display-only.
 */
import type { ReactNode } from "react";
import { Icons } from "../Icons";

export interface DataColumn<T> {
  key: string;
  label: string;
  width?: string; // e.g. "70px" or "1.4fr"
  sortable?: boolean;
  accent?: boolean; // highlight the column (e.g. primary metric)
  render: (row: T, index: number) => ReactNode;
}

export interface DataSort {
  key: string;
  dir: "asc" | "desc";
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  sort,
  onSortChange,
  selected,
  onSelect,
  onRowClick,
  selectedId,
  checkAll,
  onCheckAllChange,
  emptyLabel = "无数据",
  showCheckbox = true,
}: {
  columns: DataColumn<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string;
  sort?: DataSort | null;
  onSortChange?: (next: DataSort | null) => void;
  selected?: Set<string>;
  onSelect?: (id: string, next: boolean) => void;
  onRowClick?: (row: T) => void;
  selectedId?: string | null;
  checkAll?: boolean;
  onCheckAllChange?: (next: boolean) => void;
  emptyLabel?: string;
  showCheckbox?: boolean;
}) {
  const checkboxCol = showCheckbox ? "24px " : "";
  const gridCols = checkboxCol + columns.map((c) => c.width || "1fr").join(" ");

  const handleSort = (colKey: string) => {
    if (!onSortChange) return;
    if (!sort || sort.key !== colKey) {
      onSortChange({ key: colKey, dir: "desc" });
    } else if (sort.dir === "desc") {
      onSortChange({ key: colKey, dir: "asc" });
    } else {
      onSortChange(null);
    }
  };

  return (
    <div>
      <div className="ax-table__header" style={{ gridTemplateColumns: gridCols }}>
        {showCheckbox ? (
          <div>
            {onCheckAllChange ? (
              <input
                type="checkbox"
                checked={checkAll || false}
                onChange={(e) => onCheckAllChange(e.target.checked)}
                style={{ accentColor: "var(--ax-text-5)" }}
              />
            ) : null}
          </div>
        ) : null}
        {columns.map((c) => {
          const isSorted = sort?.key === c.key;
          const cls =
            "ax-table__header-cell" +
            (c.sortable ? " is-sortable" : "") +
            (isSorted ? " is-sorted" : "");
          return (
            <div
              key={c.key}
              className={cls}
              onClick={c.sortable ? () => handleSort(c.key) : undefined}
              style={c.accent && !isSorted ? { color: "var(--ax-status-review)" } : undefined}
            >
              {c.label}
              {c.sortable ? (
                isSorted ? (
                  sort.dir === "desc" ? (
                    <Icons.arrowDown />
                  ) : (
                    <Icons.arrowUp />
                  )
                ) : (
                  <Icons.sort style={{ opacity: 0.4 }} />
                )
              ) : null}
            </div>
          );
        })}
      </div>

      {rows.length === 0 ? (
        <div
          style={{
            padding: "32px 16px",
            textAlign: "center",
            color: "var(--ax-text-2)",
            fontSize: 11,
          }}
        >
          {emptyLabel}
        </div>
      ) : (
        rows.map((row, i) => {
          const id = rowKey(row, i);
          const isSelected = selectedId === id;
          const isChecked = selected?.has(id) || false;
          return (
            <div
              key={id}
              className={`ax-table__row${isSelected ? " is-selected" : ""}`}
              style={{ gridTemplateColumns: gridCols }}
              onClick={() => onRowClick?.(row)}
            >
              {showCheckbox ? (
                <div onClick={(e) => e.stopPropagation()}>
                  {onSelect ? (
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={(e) => onSelect(id, e.target.checked)}
                      style={{ accentColor: "var(--ax-text-5)" }}
                    />
                  ) : null}
                </div>
              ) : null}
              {columns.map((c) => (
                <div key={c.key} style={{ minWidth: 0, overflow: "hidden" }}>
                  {c.render(row, i)}
                </div>
              ))}
            </div>
          );
        })
      )}
    </div>
  );
}

export function SortBanner({
  column,
  dir,
  onClear,
}: {
  column?: string;
  dir?: "asc" | "desc";
  onClear?: () => void;
}) {
  if (!column) return null;
  return (
    <div className="ax-sort-banner">
      <Icons.sort />
      排序:{" "}
      <span className="ax-sort-banner__current">
        {column} {dir === "asc" ? "↑ 低到高" : "↓ 高到低"}
      </span>
      {onClear ? (
        <span className="ax-sort-banner__clear" onClick={onClear}>
          清除排序 ×
        </span>
      ) : null}
    </div>
  );
}

export function BulkBar({
  selectedCount,
  children,
  pager,
}: {
  selectedCount: number;
  children?: ReactNode;
  pager?: ReactNode;
}) {
  return (
    <div className="ax-bulk-bar">
      <span className="ax-bulk-bar__count">
        {selectedCount} selected
      </span>
      {children}
      {pager ? <span className="ax-bulk-bar__pager">{pager}</span> : null}
    </div>
  );
}
