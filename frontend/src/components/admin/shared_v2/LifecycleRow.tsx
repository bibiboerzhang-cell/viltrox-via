/**
 * LifecycleRow — single horizontal strip of chip selectors
 */
export interface LifecycleStage {
  key: string;
  label: string;
  count: number;
  colorVar: string; // e.g. "--ax-status-pass"
}

export function LifecycleRow({
  label = "Stage",
  totalKey = "all",
  totalLabel = "All",
  totalCount,
  stages,
  active,
  onChange,
}: {
  label?: string;
  totalKey?: string;
  totalLabel?: string;
  totalCount: number;
  stages: LifecycleStage[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="ax-lifecycle">
      <span className="ax-lifecycle__label">{label}</span>

      <button
        type="button"
        className={`ax-lifecycle__chip${active === totalKey ? " is-active" : ""}`}
        onClick={() => onChange(totalKey)}
      >
        <span className="ax-lifecycle__name">{totalLabel}</span>
        <span className="ax-lifecycle__count">{totalCount}</span>
      </button>

      {stages.map((s) => (
        <button
          key={s.key}
          type="button"
          className={`ax-lifecycle__chip${active === s.key ? " is-active" : ""}`}
          onClick={() => onChange(s.key)}
        >
          <span
            className="ax-lifecycle__dot"
            style={{ background: `var(${s.colorVar})` }}
          />
          <span className="ax-lifecycle__name">{s.label}</span>
          <span className="ax-lifecycle__count">{s.count}</span>
        </button>
      ))}
    </div>
  );
}
