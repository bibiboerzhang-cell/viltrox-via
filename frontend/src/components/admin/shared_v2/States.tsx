/**
 * States: Loading / Empty / Error cards (inside admin)
 */
import type { ReactNode } from "react";

export function LoadingCard({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="ax-card" style={{ color: "var(--ax-text-2)", textAlign: "center" }}>
      {label}
    </div>
  );
}

export function EmptyCard({
  label,
  hint,
  action,
}: {
  label: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div
      className="ax-card"
      style={{ textAlign: "center", padding: "32px 24px" }}
    >
      <div style={{ color: "var(--ax-text-5)", fontWeight: 500, marginBottom: 4 }}>
        {label}
      </div>
      {hint ? (
        <div style={{ color: "var(--ax-text-1)", fontSize: 11, marginBottom: 12 }}>
          {hint}
        </div>
      ) : null}
      {action}
    </div>
  );
}

export function ErrorCard({
  label = "加载失败",
  detail,
  onRetry,
}: {
  label?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="ax-card"
      style={{ borderColor: "rgba(209, 69, 32, 0.3)" }}
    >
      <div
        style={{
          color: "var(--ax-status-alert)",
          fontSize: 12,
          fontWeight: 500,
          marginBottom: 4,
        }}
      >
        ⚠ {label}
      </div>
      {detail ? (
        <div
          style={{
            color: "var(--ax-text-2)",
            fontSize: 11,
            fontFamily: "var(--ax-font-mono)",
          }}
        >
          {detail}
        </div>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          className="ax-btn ax-btn--sm"
          style={{ marginTop: 10 }}
          onClick={onRetry}
        >
          重试
        </button>
      ) : null}
    </div>
  );
}

export function WarningCard({
  label = "部分数据加载失败",
  detail,
}: {
  label?: string;
  detail?: string;
}) {
  return (
    <div
      className="ax-card"
      style={{ borderColor: "rgba(255, 122, 24, 0.35)", background: "rgba(255, 122, 24, 0.04)" }}
    >
      <div
        style={{
          color: "var(--ax-status-review)",
          fontSize: 12,
          fontWeight: 500,
          marginBottom: detail ? 4 : 0,
        }}
      >
        ⚠ {label}
      </div>
      {detail ? (
        <div
          style={{
            color: "var(--ax-text-2)",
            fontSize: 11,
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
          }}
        >
          {detail}
        </div>
      ) : null}
    </div>
  );
}
