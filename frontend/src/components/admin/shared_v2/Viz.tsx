/**
 * Visual primitives — heatmap, progress, section
 */
import type { ReactNode } from "react";

/** 30-day activity heatmap. values[i] ∈ [0, 1] */
export function Heatmap30({ values }: { values: number[] }) {
  const padded = [...values];
  while (padded.length < 30) padded.unshift(0);
  const bucket = (v: number) => {
    if (v <= 0) return "";
    if (v < 0.3) return "0.2";
    if (v < 0.5) return "0.4";
    if (v < 0.7) return "0.6";
    if (v < 0.9) return "0.8";
    return "1";
  };
  return (
    <div className="ax-heatmap">
      {padded.slice(-30).map((v, i) => (
        <div
          key={i}
          className="ax-heatmap__cell"
          data-i={bucket(v)}
          title={`day -${29 - i}: ${Math.round(v * 100)}%`}
        />
      ))}
    </div>
  );
}

/** VIP tier progress bar */
export function TierProgress({
  current,
  next,
  progress,
  breakdown,
}: {
  current: string;
  next?: string;
  progress: number; // 0-1
  breakdown?: string;
}) {
  const pct = Math.max(0, Math.min(1, progress));
  return (
    <div className="ax-card" style={{ padding: "8px 10px" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: 10,
          marginBottom: 4,
        }}
      >
        <span style={{ color: "var(--ax-text-2)" }}>
          {next ? `→ ${next}` : `已达 ${current} 最高`}
        </span>
        <span style={{ color: "var(--ax-text-5)", fontWeight: 500 }}>
          {Math.round(pct * 100)}%
        </span>
      </div>
      <div className="ax-progress">
        <div className="ax-progress__fill" style={{ width: `${pct * 100}%` }} />
      </div>
      {breakdown ? (
        <div
          style={{
            fontSize: 9,
            color: "var(--ax-text-1)",
            marginTop: 4,
          }}
        >
          {breakdown}
        </div>
      ) : null}
    </div>
  );
}

/** Uppercase mini section label */
export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="ax-label">{children}</div>;
}

/** Card with label + body */
export function LabeledCard({
  label,
  children,
  style,
}: {
  label: string;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div className="ax-card" style={style}>
      <SectionLabel>{label}</SectionLabel>
      {children}
    </div>
  );
}

/** Status pill (semantic) */
export function StatusPill({
  tone,
  children,
}: {
  tone:
    | "pass"
    | "review"
    | "queue"
    | "new"
    | "active"
    | "idle"
    | "churn"
    | "block"
    | "flag";
  children: ReactNode;
}) {
  return <span className={`ax-status ax-status--${tone}`}>{children}</span>;
}
