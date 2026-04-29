/**
 * Tags — inline display + picker
 */
import { useState } from "react";
import type { CSSProperties } from "react";

export function TagList({
  tags,
  onRemove,
  addable,
  onAdd,
  maxInline,
}: {
  tags: string[];
  onRemove?: (tag: string) => void;
  addable?: boolean;
  onAdd?: (tag: string) => void;
  maxInline?: number;
}) {
  const displayed = maxInline ? tags.slice(0, maxInline) : tags;
  const hidden = maxInline ? tags.length - maxInline : 0;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "center" }}>
      {displayed.map((t) => (
        <span key={t} className="ax-tag">
          {t}
          {onRemove ? (
            <span
              style={{
                marginLeft: 4,
                color: "var(--ax-text-0)",
                cursor: "pointer",
              }}
              onClick={(e) => {
                e.stopPropagation();
                onRemove(t);
              }}
            >
              ×
            </span>
          ) : null}
        </span>
      ))}
      {hidden > 0 ? (
        <span
          style={{
            color: "var(--ax-text-1)",
            fontSize: 9,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          +{hidden}
        </span>
      ) : null}
      {addable && onAdd ? <InlineTagInput onAdd={onAdd} /> : null}
    </div>
  );
}

function InlineTagInput({ onAdd }: { onAdd: (tag: string) => void }) {
  const [value, setValue] = useState("");
  const [open, setOpen] = useState(false);

  const submit = () => {
    const v = value.trim();
    if (v) onAdd(v);
    setValue("");
    setOpen(false);
  };

  if (!open) {
    return (
      <span
        className="ax-chip ax-chip--dashed"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        style={{ cursor: "pointer" }}
      >
        + tag
      </span>
    );
  }

  return (
    <input
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={submit}
      onKeyDown={(e) => {
        if (e.key === "Enter") submit();
        if (e.key === "Escape") {
          setValue("");
          setOpen(false);
        }
      }}
      placeholder="新 tag…"
      style={
        {
          background: "var(--ax-bg-0)",
          border: "0.5px solid var(--ax-border-5)",
          borderRadius: 2,
          color: "var(--ax-text-5)",
          fontSize: 9,
          padding: "1px 5px",
          width: 80,
          fontFamily: "inherit",
          outline: "none",
        } as CSSProperties
      }
    />
  );
}

export function TagPicker({
  allTags,
  selected,
  onToggle,
  onCreate,
}: {
  allTags: Array<{ label: string; count?: number }>;
  selected: Set<string>;
  onToggle: (tag: string) => void;
  onCreate?: (tag: string) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = allTags.filter((t) =>
    t.label.toLowerCase().includes(query.toLowerCase()),
  );
  const matchesExact = allTags.some((t) => t.label === query.trim());
  const canCreate = onCreate && query.trim() && !matchesExact;

  return (
    <div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
        {filtered.slice(0, 10).map((t) => {
          const isOn = selected.has(t.label);
          return (
            <span
              key={t.label}
              className="ax-chip"
              style={
                isOn
                  ? undefined
                  : {
                      background: "var(--ax-bg-3)",
                      borderColor: "var(--ax-border-3)",
                      color: "var(--ax-text-2)",
                    }
              }
              onClick={() => onToggle(t.label)}
            >
              {isOn ? "" : "+ "}
              {t.label}
              {t.count !== undefined ? (
                <span style={{ color: "var(--ax-text-0)", marginLeft: 4 }}>
                  {t.count}
                </span>
              ) : null}
              {isOn ? <span className="ax-chip__close">×</span> : null}
            </span>
          );
        })}
      </div>
      <input
        type="text"
        className="ax-range__input"
        placeholder="搜索 / 创建 tag…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && canCreate) {
            onCreate!(query.trim());
            setQuery("");
          }
        }}
        style={{ width: "100%" }}
      />
      {canCreate ? (
        <div style={{ fontSize: 9, color: "var(--ax-text-1)", marginTop: 4 }}>
          按 Enter 创建 "{query.trim()}"
        </div>
      ) : null}
    </div>
  );
}
