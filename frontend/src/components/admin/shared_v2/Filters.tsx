/**
 * Filters — collapsible filter system
 *
 * <FiltersBar>: the always-visible summary row with active chips
 * <FiltersPanel>: the expanded 5-column panel
 *
 * Usage:
 *   const [open, setOpen] = useState(false);
 *   <FiltersBar open={open} onToggle={() => setOpen(!open)}
 *     chips={activeChips} total={163} shown={28} onClear={...} />
 *   {open && <FiltersPanel>...children columns...</FiltersPanel>}
 */
import type { ReactNode } from "react";
import { Icons } from "../Icons";

export interface FilterChip {
  key: string;
  label: string;
  onRemove: () => void;
}

export function FiltersBar({
  open,
  onToggle,
  chips,
  total,
  shown,
  onClear,
  onAdd,
}: {
  open: boolean;
  onToggle: () => void;
  chips: FilterChip[];
  total: number;
  shown: number;
  onClear?: () => void;
  onAdd?: () => void;
}) {
  const ChevIcon = open ? Icons.caret : Icons.caretRight;
  return (
    <div className="ax-filters-bar">
      <button type="button" className="ax-filters-bar__toggle" onClick={onToggle}>
        <ChevIcon aria-hidden />
        <span
          style={{
            fontSize: 9,
            textTransform: "uppercase",
            letterSpacing: 0.5,
            fontWeight: 600,
            color: "var(--ax-text-0)",
          }}
        >
          Filters
        </span>
      </button>

      <div className="ax-filters-bar__chips">
        {chips.map((c) => (
          <span key={c.key} className="ax-chip">
            {c.label}
            <span
              className="ax-chip__close"
              onClick={(e) => {
                e.stopPropagation();
                c.onRemove();
              }}
            >
              ×
            </span>
          </span>
        ))}
        {onAdd ? (
          <button type="button" className="ax-filters-bar__add" onClick={onAdd}>
            + 添加
          </button>
        ) : null}
      </div>

      <span className="ax-filters-bar__count">
        显示 <span className="ax-num">{shown}</span> / <span className="ax-num">{total}</span>
      </span>
      {onClear && chips.length > 0 ? (
        <button type="button" className="ax-filters-bar__clear" onClick={onClear}>
          清除全部
        </button>
      ) : null}
    </div>
  );
}

export function FiltersPanel({ children }: { children: ReactNode }) {
  return (
    <div className="ax-filters-panel">
      <div className="ax-filters-panel__grid">{children}</div>
    </div>
  );
}

export function FilterGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="ax-label">{label}</div>
      {children}
    </div>
  );
}

export function FilterCheck({
  label,
  count,
  checked,
  onChange,
  dot,
  sub,
}: {
  label: string;
  count?: number;
  checked: boolean;
  onChange: (next: boolean) => void;
  dot?: ReactNode;
  sub?: string;
}) {
  return (
    <label className={`ax-check${checked ? " is-on" : ""}`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {dot}
      <span>{label}</span>
      {sub ? (
        <span style={{ color: "var(--ax-text-0)", fontSize: 9 }}>{sub}</span>
      ) : null}
      {count !== undefined ? <span className="ax-check__count">{count}</span> : null}
    </label>
  );
}

export function RangeInput({
  label,
  minValue,
  maxValue,
  onChange,
}: {
  label: string;
  minValue: string;
  maxValue: string;
  onChange: (next: { min: string; max: string }) => void;
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <span className="ax-range__label">{label}</span>
      <div className="ax-range">
        <input
          type="number"
          className="ax-range__input"
          value={minValue}
          onChange={(e) => onChange({ min: e.target.value, max: maxValue })}
        />
        <span className="ax-range__sep">→</span>
        <input
          type="number"
          className="ax-range__input"
          value={maxValue}
          onChange={(e) => onChange({ min: minValue, max: e.target.value })}
        />
      </div>
    </div>
  );
}

export function SegButton({
  items,
  active,
  onChange,
}: {
  items: Array<{ key: string; label: string }>;
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="ax-seg">
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          className={`ax-seg__btn${active === it.key ? " is-active" : ""}`}
          onClick={() => onChange(it.key)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
