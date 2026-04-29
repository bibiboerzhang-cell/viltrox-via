/**
 * KPIGrid — row of KPI tiles
 */
export interface KPI {
  label: string;
  value: string | number;
  delta?: { text: string; tone: "up" | "down" | "flat" };
  hint?: string;
}

export function KPIGrid({ items, columns = 4 }: { items: KPI[]; columns?: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${columns}, 1fr)`,
        gap: 10,
      }}
    >
      {items.map((k, i) => (
        <div key={i} className="ax-kpi">
          <div className="ax-kpi__label">
            <span>{k.label}</span>
            {k.hint ? (
              <span style={{ color: "var(--ax-text-0)", fontSize: 9 }}>{k.hint}</span>
            ) : null}
          </div>
          <div className="ax-kpi__value">{k.value}</div>
          {k.delta ? (
            <div className={`ax-kpi__delta ax-kpi__delta--${k.delta.tone}`}>
              {k.delta.text}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
