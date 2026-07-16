import React from "react";
import { Plus, X } from "lucide-react";

import type { AdvisorContextRef } from "../../../../services/vkpi/marketing-advisor-api";

const CONTEXT_TYPES: Array<{ value: AdvisorContextRef["entity_type"]; label: string }> = [
  { value: "kol", label: "KOL" },
  { value: "product", label: "产品" },
  { value: "project", label: "项目" },
  { value: "event", label: "活动" },
  { value: "dealer", label: "Dealer" },
];

export function MarketingAdvisorContextPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: AdvisorContextRef[];
  onChange: (next: AdvisorContextRef[]) => void;
  disabled?: boolean;
}) {
  const [entityType, setEntityType] = React.useState<AdvisorContextRef["entity_type"]>("kol");
  const [entityId, setEntityId] = React.useState("");
  const [label, setLabel] = React.useState("");

  const add = () => {
    const safeId = entityId.trim();
    if (!safeId || disabled) return;
    const observedAt = new Date().toISOString();
    const next: AdvisorContextRef = {
      entity_type: entityType,
      entity_id: safeId,
      snapshot: {
        label: label.trim() || safeId,
        observed_at: observedAt,
      },
      provenance: {
        source_ref: "explicit:advisor-workspace-context",
        observed_at: observedAt,
      },
    };
    onChange([
      ...value.filter((item) => !(item.entity_type === entityType && item.entity_id === safeId)),
      next,
    ].slice(-12));
    setEntityId("");
    setLabel("");
  };

  return (
    <fieldset className="mb-3 rounded-xl border border-line bg-panel px-3 py-2" aria-label="顾问上下文">
      <legend className="px-1 text-[10px] font-semibold text-ink-2">本轮真实上下文（可选）</legend>
      <div className="grid gap-2 sm:grid-cols-[110px_minmax(0,1fr)_minmax(0,1.2fr)_auto]">
        <select
          value={entityType}
          onChange={(event) => setEntityType(event.target.value as AdvisorContextRef["entity_type"])}
          disabled={disabled}
          className="min-h-9 rounded-lg border border-line bg-card px-2 text-[10.5px] text-ink outline-none focus:border-accent"
          aria-label="上下文类型"
        >
          {CONTEXT_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
        <input
          value={entityId}
          onChange={(event) => setEntityId(event.target.value)}
          disabled={disabled}
          placeholder="实体 ID / handle"
          className="min-h-9 rounded-lg border border-line bg-card px-2.5 text-[10.5px] text-ink outline-none placeholder:text-muted focus:border-accent"
          aria-label="上下文实体 ID"
        />
        <input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          disabled={disabled}
          placeholder="显示标签（例如：ItiJarve）"
          className="min-h-9 rounded-lg border border-line bg-card px-2.5 text-[10.5px] text-ink outline-none placeholder:text-muted focus:border-accent"
          aria-label="上下文显示标签"
        />
        <button
          type="button"
          onClick={add}
          disabled={disabled || !entityId.trim()}
          className="inline-flex min-h-9 items-center justify-center gap-1 rounded-lg border border-accent bg-accent-soft px-2.5 text-[10.5px] font-semibold text-accent disabled:border-line disabled:bg-card disabled:text-muted"
          aria-label="添加顾问上下文"
        >
          <Plus size={12} /> 添加
        </button>
      </div>
      {value.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5" aria-label="已选择顾问上下文">
          {value.map((item) => (
            <span key={`${item.entity_type}:${item.entity_id}`} className="inline-flex items-center gap-1 rounded-full border border-accent bg-accent-soft px-2 py-1 text-[9.5px] text-accent">
              {item.entity_type.toUpperCase()} · {item.snapshot?.label || item.entity_id}
              <button
                type="button"
                onClick={() => onChange(value.filter((current) => current !== item))}
                disabled={disabled}
                className="rounded-full text-muted hover:text-crit"
                aria-label={`移除上下文：${item.snapshot?.label || item.entity_id}`}
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <div className="mt-1.5 text-[9.5px] text-muted">不填则只使用当前员工已授权的私有会话与记忆证据。</div>
      )}
    </fieldset>
  );
}
