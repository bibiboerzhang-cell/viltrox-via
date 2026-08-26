import { ChevronDown, SlidersHorizontal } from "lucide-react";

import type { AutoRelaxView } from "./SmartKolInputPanel.AutoRelax";
import { AutoRelaxNotice } from "./SmartKolInputPanel.AutoRelaxNotice";
import { COUNTRY_INFO } from "../data/countryInfo";
import {
  SMART_KOL_LANGUAGE_OPTIONS,
  SMART_KOL_MAX_LANGUAGES,
} from "./SmartKolInputPanel.QualityFilters";

export const KOL_SEARCH_RESULT_LIMIT = 30;

export type KolSearchStrategy = "vertical" | "balanced" | "expansion";

/**
 * 每平台在线发现上限。YouTube 那条腿走 YouTube Data API search.list：
 * 同一次调用无论 maxResults 取 1 还是 50 都恒定 100 quota units、仍是一次 HTTP 往返，
 * 所以 20→50 配额不变、延迟不变、零 Apify 花费（prod 实测该腿 <2s）。
 * Instagram / TikTok 走按结果计费的 Apify actor —— prod 14 天实测 IG hashtag 一家
 * 就吃掉在线发现总花费的 93.7%（29 次 run／$16.57），且它的 resultsLimit 是按 tag 计的
 * （单次 dataset 实测 240~300 条 = 4~5 个 tag × 60），上限翻 2.5 倍成本同步翻倍。
 * 所以本批只提 YouTube，IG/TT 维持 20 不动。
 */
export const KOL_SEARCH_PER_PLATFORM_LIMITS: Readonly<Record<string, number>> = Object.freeze({
  youtube: 50,
  instagram: 20,
  tiktok: 20,
});
export type GearContentFilter = "any" | "yes" | "no";

export interface KolSearchFilterState {
  country: string;
  followersMin: string;
  followersMax: string;
  vertical: string;
  gearContent: GearContentFilter;
}

export interface KolSearchApiFilters {
  platforms?: string[];
  countries?: string[];
  languages?: string[];
  followers_min?: number;
  followers_max?: number;
  verticals?: string[];
  gear_content?: GearContentFilter;
}

export interface KolSearchBucketPolicy {
  core_vertical: number;
  expansion: number;
  exploration: number;
}

export interface KolSearchStrategyPolicy {
  key: KolSearchStrategy;
  label: string;
  short: string;
  description: string;
  legacyMode: "precision" | "balanced" | "discovery";
  creatorQuota: number;
  reviewerQuota: number;
  newDiscoveryLimit: number;
  /** 未在 perPlatformLimits 里显式列出的平台使用的兜底上限。 */
  perPlatformLimit: number;
  /** 每平台上限覆盖（{平台: 上限}），随请求体透传给后端。 */
  perPlatformLimits: Readonly<Record<string, number>>;
  bucketPolicy: KolSearchBucketPolicy;
}

export const KOL_SEARCH_STRATEGIES: Record<KolSearchStrategy, KolSearchStrategyPolicy> = {
  vertical: {
    key: "vertical",
    label: "垂直优先",
    short: "核心 24 · 拓展 5 · 探索 1",
    description: "优先镜头测评、摄影教程、器材对比和相机系统内容；严格结果不足会明确显示补位。",
    legacyMode: "precision",
    creatorQuota: 9,
    reviewerQuota: 21,
    newDiscoveryLimit: 45,
    perPlatformLimit: 20,
    perPlatformLimits: KOL_SEARCH_PER_PLATFORM_LIMITS,
    bucketPolicy: { core_vertical: 24, expansion: 5, exploration: 1 },
  },
  balanced: {
    key: "balanced",
    label: "平衡",
    short: "核心 18 · 拓展 9 · 探索 3",
    description: "兼顾垂直专业度和生活方式、Vlog 等拓展方向，默认返回 30 位筛选后候选人。",
    legacyMode: "balanced",
    creatorQuota: 18,
    reviewerQuota: 12,
    newDiscoveryLimit: 45,
    perPlatformLimit: 20,
    perPlatformLimits: KOL_SEARCH_PER_PLATFORM_LIMITS,
    bucketPolicy: { core_vertical: 18, expansion: 9, exploration: 3 },
  },
  expansion: {
    key: "expansion",
    label: "拓展",
    short: "核心 15 · 拓展 12 · 探索 3",
    description: "扩大生活方式、Vlog、科技和跨圈层人群，同时保留最低垂直候选比例。",
    legacyMode: "discovery",
    creatorQuota: 21,
    reviewerQuota: 9,
    newDiscoveryLimit: 50,
    perPlatformLimit: 20,
    perPlatformLimits: KOL_SEARCH_PER_PLATFORM_LIMITS,
    bucketPolicy: { core_vertical: 15, expansion: 12, exploration: 3 },
  },
};

export const EMPTY_KOL_SEARCH_FILTERS: KolSearchFilterState = {
  country: "",
  followersMin: "",
  followersMax: "",
  vertical: "",
  gearContent: "any",
};

export function strategyFromLegacyMode(mode: string): KolSearchStrategy {
  if (mode === "precision" || mode === "vertical") return "vertical";
  if (mode === "discovery" || mode === "expansion") return "expansion";
  return "balanced";
}

function positiveNumber(raw: string): number | undefined {
  if (!String(raw || "").trim()) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? Math.floor(value) : undefined;
}

const SMART_KOL_LANGUAGE_CODES = new Set(SMART_KOL_LANGUAGE_OPTIONS.map((option) => option.value));

export function normalizeKolSearchLanguages(values: readonly string[]): string[] {
  return Array.from(new Set(
    values
      .map((value) => String(value || "").trim().toLowerCase())
      .filter((value) => SMART_KOL_LANGUAGE_CODES.has(value)),
  )).sort().slice(0, SMART_KOL_MAX_LANGUAGES);
}

export function toKolSearchApiFilters(
  state: KolSearchFilterState,
  platforms: string[],
  languages: readonly string[] = [],
): KolSearchApiFilters {
  const followersMin = positiveNumber(state.followersMin);
  const followersMax = positiveNumber(state.followersMax);
  const canonicalLanguages = normalizeKolSearchLanguages(languages);
  return {
    ...(platforms.length ? { platforms } : {}),
    ...(state.country ? { countries: [state.country] } : {}),
    ...(canonicalLanguages.length ? { languages: canonicalLanguages } : {}),
    ...(followersMin != null ? { followers_min: followersMin } : {}),
    ...(followersMax != null ? { followers_max: followersMax } : {}),
    ...(state.vertical ? { verticals: [state.vertical] } : {}),
    ...(state.gearContent !== "any" ? { gear_content: state.gearContent } : {}),
  };
}

export function activeKolSearchFilterCount(
  state: KolSearchFilterState,
  platforms: string[],
  languages: readonly string[] = [],
): number {
  return Number(platforms.length > 0)
    + Number(Boolean(state.country))
    + Number(normalizeKolSearchLanguages(languages).length > 0)
    + Number(Boolean(state.followersMin || state.followersMax))
    + Number(Boolean(state.vertical))
    + Number(state.gearContent !== "any");
}

const PLATFORM_OPTIONS = [
  { key: "youtube", label: "YouTube" },
  { key: "instagram", label: "Instagram" },
  { key: "tiktok", label: "TikTok" },
  { key: "facebook", label: "Facebook" },
];

const VERTICAL_OPTIONS = [
  ["", "全部垂类"],
  ["lens_review", "镜头评测"],
  ["photography_tutorial", "摄影教程"],
  ["gear_comparison", "器材对比"],
  ["portrait", "人像创作"],
  ["video_creation", "视频创作"],
  ["camera_system", "相机系统"],
  ["vlog", "Vlog"],
  ["lifestyle", "生活方式"],
  ["technology", "科技"],
];

export function KolSearchPolicyPanel({
  open,
  onToggleOpen,
  strategy,
  onStrategyChange,
  platforms,
  onPlatformsChange,
  languages,
  onLanguagesChange,
  filters,
  onFiltersChange,
  autoRelax = null,
  onAutoRelaxRestore,
  onAutoRelaxRemoveAdded,
  autoRelaxRemovedKeys = [],
  autoRelaxBusy = false,
}: {
  open: boolean;
  onToggleOpen: () => void;
  strategy: KolSearchStrategy;
  onStrategyChange: (strategy: KolSearchStrategy) => void;
  platforms: string[];
  onPlatformsChange: (platforms: string[]) => void;
  languages: readonly string[];
  onLanguagesChange: (languages: string[]) => void;
  filters: KolSearchFilterState;
  onFiltersChange: (filters: KolSearchFilterState) => void;
  /** 上一次搜索里系统自动放宽了什么、又替操作员加了什么。挂在筛选面板上——说的就是这些控件本身。 */
  autoRelax?: AutoRelaxView | null;
  onAutoRelaxRestore?: () => void;
  /** 去掉系统加的某一条(操作员从没说过的那种)。 */
  onAutoRelaxRemoveAdded?: (key: string) => void;
  autoRelaxRemovedKeys?: string[];
  autoRelaxBusy?: boolean;
}) {
  const policy = KOL_SEARCH_STRATEGIES[strategy];
  const canonicalLanguages = normalizeKolSearchLanguages(languages);
  const languageSelectValue = canonicalLanguages.length > 1 ? "__multiple__" : canonicalLanguages[0] || "";
  const activeCount = activeKolSearchFilterCount(filters, platforms, canonicalLanguages);
  const update = <K extends keyof KolSearchFilterState>(key: K, value: KolSearchFilterState[K]) => {
    onFiltersChange({ ...filters, [key]: value });
  };

  return (
    <div className="mt-2 rounded-lg border border-white/[0.065] bg-white/[0.018] p-2.5" data-testid="kol-search-policy-panel">
      <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="mr-0.5 text-[10px] text-slate-500">搜索策略</span>
          {(Object.values(KOL_SEARCH_STRATEGIES) as KolSearchStrategyPolicy[]).map((item) => {
            const selected = item.key === strategy;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => onStrategyChange(item.key)}
                title={item.description}
                aria-pressed={selected}
                className={`rounded-md border px-2 py-1 text-[10px] transition-colors ${selected
                  ? "border-cyan-300/35 bg-cyan-400/[0.12] text-cyan-100"
                  : "border-white/[0.08] bg-black/10 text-slate-500 hover:border-white/[0.16] hover:text-slate-300"}`}
              >
                <span className="font-medium">{item.label}</span>
                {selected ? <span className="ml-1 text-[8.5px] opacity-75">{item.short}</span> : null}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded-md border border-emerald-300/20 bg-emerald-400/[0.07] px-2 py-1 text-[9.5px] font-medium text-emerald-100">
            筛选后目标 {KOL_SEARCH_RESULT_LIMIT} 人
          </span>
          <button
            type="button"
            onClick={onToggleOpen}
            aria-expanded={open}
            className="inline-flex items-center gap-1.5 rounded-md border border-white/[0.08] bg-black/10 px-2 py-1 text-[9.5px] text-slate-400 transition-colors hover:border-white/[0.16] hover:text-white"
          >
            <SlidersHorizontal size={10} /> 搜索前筛选{activeCount ? ` · ${activeCount}` : ""}
            <ChevronDown size={10} className={`transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>

      {/* 折叠与否都显示:自动放宽**和自动加筛选**都是替操作员做的决定,不能藏在抽屉里。 */}
      <AutoRelaxNotice
        view={autoRelax}
        onRestore={onAutoRelaxRestore}
        onRemoveAdded={onAutoRelaxRemoveAdded}
        removedKeys={autoRelaxRemovedKeys}
        busy={autoRelaxBusy}
      />

      {open ? (
        <div className="mt-2 space-y-2 border-t border-white/[0.05] pt-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-12 text-[9.5px] text-slate-500">平台</span>
            {PLATFORM_OPTIONS.map((item) => {
              const selected = platforms.includes(item.key);
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onPlatformsChange(selected ? platforms.filter((key) => key !== item.key) : [...platforms, item.key])}
                  className={`rounded-full border px-2 py-0.5 text-[9.5px] transition-colors ${selected
                    ? "border-violet-300/35 bg-violet-400/[0.10] text-violet-100"
                    : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"}`}
                >{item.label}</button>
              );
            })}
            {!platforms.length ? <span className="text-[9px] text-amber-200/75">未限定平台</span> : null}
          </div>

          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <label className="space-y-1 text-[9.5px] text-slate-500">
              <span>国家 / 地区</span>
              <select
                aria-label="国家或地区"
                value={filters.country}
                onChange={(event) => update("country", event.target.value)}
                className="w-full rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-cyan-300/35"
              >
                <option value="">全部地区</option>
                {Object.entries(COUNTRY_INFO).map(([key, value]) => (
                  <option key={key} value={key}>{value.flag} {value.name} · {key}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-[9.5px] text-slate-500">
              <span>内容语言 · 同严格硬筛</span>
              <select
                aria-label="内容语言"
                value={languageSelectValue}
                onChange={(event) => {
                  const value = event.target.value;
                  if (value !== "__multiple__") onLanguagesChange(value ? [value] : []);
                }}
                className="w-full rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-cyan-300/35"
              >
                <option value="">全部语言</option>
                {canonicalLanguages.length > 1 ? (
                  <option value="__multiple__" disabled>已选 {canonicalLanguages.length} 种语言 · 在严格筛选中管理</option>
                ) : null}
                {SMART_KOL_LANGUAGE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-[9.5px] text-slate-500">
              <span>内容垂类</span>
              <select
                aria-label="内容垂类"
                value={filters.vertical}
                onChange={(event) => update("vertical", event.target.value)}
                className="w-full rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none focus:border-cyan-300/35"
              >
                {VERTICAL_OPTIONS.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}
              </select>
            </label>
            <div className="space-y-1 text-[9.5px] text-slate-500">
              <span>粉丝区间</span>
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-1">
                <input
                  aria-label="最低粉丝数"
                  inputMode="numeric"
                  min={0}
                  type="number"
                  value={filters.followersMin}
                  onChange={(event) => update("followersMin", event.target.value)}
                  placeholder="最低"
                  className="min-w-0 rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none placeholder-slate-600 focus:border-cyan-300/35"
                />
                <span className="text-slate-700">–</span>
                <input
                  aria-label="最高粉丝数"
                  inputMode="numeric"
                  min={0}
                  type="number"
                  value={filters.followersMax}
                  onChange={(event) => update("followersMax", event.target.value)}
                  placeholder="最高"
                  className="min-w-0 rounded-md border border-white/[0.08] bg-black/30 px-2 py-1.5 text-[10px] text-slate-200 outline-none placeholder-slate-600 focus:border-cyan-300/35"
                />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 text-[9.5px] text-slate-500">器材内容证据</span>
            {([
              ["any", "不限制"],
              ["yes", "发布过镜头/器材"],
              ["no", "未发现器材内容"],
            ] as Array<[GearContentFilter, string]>).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filters.gearContent === value}
                onClick={() => update("gearContent", value)}
                className={`rounded-md border px-2 py-0.5 text-[9.5px] transition-colors ${filters.gearContent === value
                  ? "border-amber-300/35 bg-amber-400/[0.10] text-amber-100"
                  : "border-white/[0.08] text-slate-500 hover:border-white/[0.16]"}`}
              >{label}</button>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-[9px] leading-relaxed text-slate-600">
            <span>{policy.description}</span>
            <span>数据缺失或后端未应用的条件会在结果区明确标注，不会伪装成已筛选。</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
