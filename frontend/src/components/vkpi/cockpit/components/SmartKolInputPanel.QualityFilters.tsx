type FilterOption = Readonly<{ value: string; label: string }>;

export const SMART_KOL_MAX_LANGUAGES = 8;

export const SMART_KOL_LANGUAGE_OPTIONS: readonly FilterOption[] = Object.freeze([
  { value: "en", label: "英语" },
  { value: "ja", label: "日语" },
  { value: "ko", label: "韩语" },
  { value: "de", label: "德语" },
  { value: "fr", label: "法语" },
  { value: "es", label: "西语" },
  { value: "pt", label: "葡语" },
  { value: "it", label: "意语" },
  { value: "ru", label: "俄语" },
  { value: "th", label: "泰语" },
  { value: "vi", label: "越语" },
  { value: "id", label: "印尼语" },
  { value: "ms", label: "马来语" },
  { value: "nl", label: "荷兰语" },
  { value: "pl", label: "波兰语" },
  { value: "sv", label: "瑞典语" },
  { value: "tr", label: "土耳其语" },
  { value: "zh", label: "中文" },
  { value: "ar", label: "阿语" },
]);

export const SMART_KOL_TYPE_OPTIONS: readonly FilterOption[] = Object.freeze([
  { value: "creator", label: "内容创作者" },
  { value: "reviewer", label: "器材评测" },
  { value: "mixed", label: "创作+评测" },
]);

function toggleValue(values: readonly string[], value: string): string[] {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value];
}

function FilterChips({
  label,
  options,
  selected,
  onChange,
  maxSelected,
}: {
  label: string;
  options: readonly FilterOption[];
  selected: readonly string[];
  onChange: (values: string[]) => void;
  maxSelected?: number;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[10px] text-slate-500">{label}</span>
      {options.map((option) => {
        const active = selected.includes(option.value);
        const limitReached = !active && typeof maxSelected === "number" && selected.length >= maxSelected;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={limitReached}
            title={limitReached ? `最多选择 ${maxSelected} 种内容语言` : undefined}
            onClick={() => onChange(toggleValue(selected, option.value))}
            className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${
              active
                ? "border-violet-300/40 bg-violet-400/[0.12] text-violet-100"
                : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"
            }`}
          >
            {option.label}
          </button>
        );
      })}
      {selected.length ? (
        <button
          type="button"
          onClick={() => onChange([])}
          className="px-1 text-[9px] text-slate-500 hover:text-slate-300"
        >
          清除
        </button>
      ) : (
        <span className="text-[9px] text-slate-600">不限</span>
      )}
    </div>
  );
}

export function SmartKolQualityFilters({
  languages,
  profileTypes,
  onLanguagesChange,
  onProfileTypesChange,
}: {
  languages: readonly string[];
  profileTypes: readonly string[];
  onLanguagesChange: (values: string[]) => void;
  onProfileTypesChange: (values: string[]) => void;
}) {
  return (
    <div
      className="flex flex-col gap-1.5 rounded-md border border-white/[0.07] bg-black/15 px-2.5 py-2"
      data-testid="smart-kol-quality-filters"
    >
      <div className="flex flex-wrap items-center justify-between gap-1 text-[9px] text-slate-500">
        <span>内容语言与 KOL 类型 · 显式选择后由服务端硬筛</span>
        <span>未知证据不计入 30</span>
      </div>
      <FilterChips
        label={`内容语言（不选则不限，最多 ${SMART_KOL_MAX_LANGUAGES} 种）`}
        options={SMART_KOL_LANGUAGE_OPTIONS}
        selected={languages}
        onChange={onLanguagesChange}
        maxSelected={SMART_KOL_MAX_LANGUAGES}
      />
      <FilterChips
        label="KOL 类型"
        options={SMART_KOL_TYPE_OPTIONS}
        selected={profileTypes}
        onChange={onProfileTypesChange}
      />
    </div>
  );
}
