import type { ReactNode } from "react";

import { EmptyState } from "../ui";

export function toNumber(value: unknown, fallback = 0): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function compactNumber(value: unknown): string {
  return toNumber(value).toLocaleString();
}

export function percentLabel(value: unknown): string {
  return `${Math.round(toNumber(value) * 100)}%`;
}

export function formatDate(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "—";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function formatDateTime(value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "—";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return `${formatDate(raw)} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function titleCase(value: string) {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((item) => item.charAt(0).toUpperCase() + item.slice(1))
    .join(" ");
}

export function parseRecord(value: unknown): Record<string, unknown> {
  if (!value) {
    return {};
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return {};
    }
  }
  return {};
}

export function toneForStatus(status: string): "neutral" | "success" | "warning" | "danger" {
  const normalized = String(status || "").toLowerCase();
  if (normalized.includes("success") || normalized.includes("approved") || normalized.includes("active") || normalized.includes("healthy") || normalized.includes("live")) {
    return "success";
  }
  if (normalized.includes("hold") || normalized.includes("pending") || normalized.includes("review") || normalized.includes("staged")) {
    return "warning";
  }
  if (normalized.includes("reject") || normalized.includes("fail") || normalized.includes("revoke") || normalized.includes("rollback") || normalized.includes("error")) {
    return "danger";
  }
  return "neutral";
}

export function JsonInfoList({
  payload,
  emptyTitle = "No runtime fields yet",
  emptyBody = "This block will hydrate once the backend returns a richer snapshot.",
}: {
  payload: Record<string, unknown> | null | undefined;
  emptyTitle?: string;
  emptyBody?: string;
}) {
  const entries = Object.entries(payload || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  if (!entries.length) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }
  return (
    <div className="info-list">
      {entries.map(([key, value]) => (
        <div key={key}>
          <strong>{titleCase(key)}</strong>
          <span>{typeof value === "object" ? JSON.stringify(value) : String(value)}</span>
        </div>
      ))}
    </div>
  );
}

export function DataTable({
  columns,
  rows,
  empty,
  emptyTitle = "No rows yet",
}: {
  columns: string[];
  rows: Array<Array<ReactNode>>;
  empty: string;
  emptyTitle?: string;
}) {
  return rows.length ? (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${columns[0]}-${index}`}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <EmptyState title={emptyTitle} body={empty} />
  );
}

export function TablePager({
  page,
  totalPages,
  totalItems,
  label,
  onChange,
}: {
  page: number;
  totalPages: number;
  totalItems: number;
  label: string;
  onChange: (next: number) => void;
}) {
  if (totalPages <= 1) {
    return (
      <div className="table-pager table-pager--static">
        <span className="table-pager__meta">
          {label} · {totalItems} rows
        </span>
      </div>
    );
  }
  return (
    <div className="table-pager">
      <span className="table-pager__meta">
        {label} · page {page} / {totalPages} · {totalItems} rows
      </span>
      <div className="table-actions">
        <button className="outline-btn" type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          Previous
        </button>
        <button className="outline-btn" type="button" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}
