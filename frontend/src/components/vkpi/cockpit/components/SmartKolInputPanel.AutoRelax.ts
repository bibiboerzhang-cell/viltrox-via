/**
 * 「自动放宽」的门面口径(2026-08-26)。
 *
 * 后端 `search_auto_relax.py` 只回结构化事实(松了哪一项 / 松之前多少人 / 松之后多少人),
 * **一句面向操作员的话都不产出**。所有中文都在这里拼——门面文案只有一处真源,
 * 也保证界面说的每个数字都等于系统真做过的事。
 *
 * 四条自我约束:
 * 1. 界面禁内部词:不出现任何取值键名、模式名、厂商名、模型名;
 * 2. 只说做过的事:没有 `applied` 就绝不说「放宽了」,人数一律取后端的实测计数;
 *    估不出人数时说的是**真实发生的事**(一格都没放宽),不说「条件按原样执行了」这种
 *    似是而非的话 —— 2026-08-26 复核纠偏;
 * 3. **加筛选与松筛选同等可见**:系统替操作员加了他没说过的条件,与系统松了他的条件
 *    一样要播报「加了什么 / 为什么加 / 怎么去掉」。上一版只播报松绑,系统能悄悄替操作员
 *    加条件而他毫不知情 —— 这比不松绑更严重,现在两边对称;
 * 4. 一键可还原,而且**任何状态下都能还原**:`optOut` 打开后这次搜索既不放宽、也不采纳
 *    系统加的条件(两个开关一起送),界面上明说现在是哪种状态。
 */
import { useRef, useState } from "react";

import { SMART_KOL_LANGUAGE_OPTIONS } from "./SmartKolInputPanel.QualityFilters";

/** 与后端 `search_auto_relax.SCHEMA` 对齐。对不上就整块不显示,绝不猜。 */
export const AUTO_RELAX_SCHEMA = "kol_search_auto_relax_v1";

export type AutoRelaxStatus = "not_needed" | "relaxed" | "short" | "disabled" | "unavailable";
export type AutoRelaxAction = "include_unknown" | "lower" | "drop";

export interface AutoRelaxStepPayload {
  key: string;
  action: AutoRelaxAction;
  count_before: number;
  count_after: number;
  gained: number;
  gained_are_unknown_only: boolean;
  from_value?: number | null;
  to_value?: number | null;
}

/** 系统替操作员加的一条硬筛(他没说过)。与 `AutoRelaxStepPayload` 对称。 */
export interface AutoRelaxAddedPayload {
  key: string;
  /** 为什么加这一条(后端从提议侧原样透传的话)。空串 = 说不出理由,门面自己兜底。 */
  reason?: string;
  values?: string[] | null;
  value?: number | null;
  removable?: boolean;
  dropped?: boolean;
}

export interface AutoRelaxPayload {
  schema?: string;
  status?: AutoRelaxStatus;
  target?: number;
  baseline_count?: number | null;
  final_count?: number | null;
  applied?: AutoRelaxStepPayload[];
  /** 系统加了哪几项操作员没说过的条件。空 = 这次一条都没加。 */
  added?: AutoRelaxAddedPayload[];
  /** 操作员点掉之后真的去掉了哪几项。 */
  added_dropped?: AutoRelaxAddedPayload[];
  skipped?: Array<{ key: string; reason: string }>;
  protected_untouched?: string[];
  advice_source?: "model" | "rules";
  /** 产量口径说明(「这是库内可选人数;联网还能补多少人不在此列」)。原样显示,不改写。 */
  scope_note?: string;
  pool_total?: number;
}

export interface AutoRelaxLine {
  key: string;
  text: string;
}

/** 一条「系统替你加的条件」在界面上的样子:加了什么 + 为什么加 + 能不能去掉。 */
export interface AutoRelaxAddedLine extends AutoRelaxLine {
  reason: string;
  removable: boolean;
}

export interface AutoRelaxView {
  tone: "relaxed" | "short" | "plain";
  headline: string;
  lines: AutoRelaxLine[];
  /** 「系统替你加了 N 项你没说过的条件」。空串 = 这次一条都没加。 */
  addedHeadline: string;
  /** 加项明细。与 `lines`(松项明细)同等地位,一样显示、一样可撤。 */
  addedLines: AutoRelaxAddedLine[];
  /** 「已按你的要求去掉了系统加的这几条」。空串 = 没去掉过。 */
  droppedNote: string;
  /** 单条加项上那颗按钮的字。 */
  removeLabel: string;
  /** 人数是按什么范围算的。空串 = 这次没有可说的口径。 */
  scopeNote: string;
  /** 「这些合格线任何时候都不会自动放宽」——空串表示这次没有可说的合格线。 */
  protectedNote: string;
  /** 条件是读懂描述算出来的,还是退回固定规则给的。降级必须如实说。 */
  sourceNote: string;
  /** 还原按钮的字。null = 这一档不提供还原(比如根本没放宽过)。 */
  restoreLabel: string | null;
}

/** 认得的状态。认不出的一律整块不显示,绝不猜。 */
const KNOWN_STATUSES: readonly string[] = Object.freeze([
  "not_needed", "relaxed", "short", "disabled", "unavailable",
]);

const FILTER_LABELS: Readonly<Record<string, string>> = Object.freeze({
  languages: "内容语言",
  countries: "国家 / 地区",
  verticals: "内容垂类",
  followers_min: "粉丝下限",
  platforms: "平台",
});

const PROTECTED_LABELS: Readonly<Record<string, string>> = Object.freeze({
  gear_content: "器材内容证据",
  freshness_days: "内容新鲜度",
  recency_days: "内容新鲜度",
  max_age_days: "内容新鲜度",
  evidence_min_terms: "证据充分度",
  min_evidence_terms: "证据充分度",
  product_anchor: "产品对应关系",
  require_product_anchor: "产品对应关系",
  brand_safety: "账号安全性",
  account_safety: "账号安全性",
  quality_floor: "质量下限",
});

function labelOf(key: string): string {
  return FILTER_LABELS[key] || "";
}

/** 取值说人话。与筛选面板同源:语言表直接复用,垂类 / 平台照筛选面板抄,国家退回大写代码。 */
const VALUE_LABELS: Readonly<Record<string, string>> = Object.freeze({
  ...Object.fromEntries(SMART_KOL_LANGUAGE_OPTIONS.map((option) => [option.value, option.label])),
  lens_review: "镜头评测",
  photography_tutorial: "摄影教程",
  gear_comparison: "器材对比",
  portrait: "人像创作",
  video_creation: "视频创作",
  camera_system: "相机系统",
  vlog: "Vlog",
  lifestyle: "生活方式",
  technology: "科技",
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  facebook: "Facebook",
});

/** 加项加的到底是什么值。翻不出中文的(国家代码)就大写原样显示,绝不显示内部小写 id。 */
function addedValuesText(added: AutoRelaxAddedPayload): string {
  if (added.key === "followers_min") return followerText(added.value);
  const values = (Array.isArray(added.values) ? added.values : []).map((value) => String(value || "").trim()).filter(Boolean);
  if (!values.length) return "";
  return values.map((value) => VALUE_LABELS[value.toLowerCase()] || value.toUpperCase()).join("、");
}

function addedLine(added: AutoRelaxAddedPayload): AutoRelaxAddedLine | null {
  const label = labelOf(added.key);
  if (!label) return null;
  const values = addedValuesText(added);
  return {
    key: `added:${added.key}`,
    text: values
      ? `系统替你加了「${label}」：${values}。这一条你没说过。`
      : `系统替你加了「${label}」这一条。你没说过它。`,
    // 理由由提议侧给;给不出就如实说给不出,不替它编一个。
    reason: String(added.reason || "").trim() || "系统没能说清为什么加这一条。",
    removable: added.removable !== false,
  };
}

function addedHeadlineOf(added: AutoRelaxAddedLine[]): string {
  if (!added.length) return "";
  return `系统按你的描述替你加了 ${added.length} 项你没说过的条件，每一条都能单独去掉。`;
}

function droppedNoteOf(dropped: AutoRelaxAddedPayload[]): string {
  const names = Array.from(new Set(dropped.map((item) => labelOf(item.key)).filter(Boolean)));
  if (!names.length) return "";
  return `已按你的要求去掉系统加的${names.map((name) => `「${name}」`).join("、")}，这次没有用它们筛。`;
}

/** 粉丝数说人话:50000 → 「5 万」。 */
export function followerText(value: number | null | undefined): string {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return "不限";
  if (parsed < 10000) return `${Math.round(parsed)}`;
  const inWan = parsed / 10000;
  return `${Number.isInteger(inWan) ? inWan : inWan.toFixed(1)} 万`;
}

function stepLine(step: AutoRelaxStepPayload): AutoRelaxLine | null {
  const label = labelOf(step.key);
  if (!label) return null;
  const gained = Math.max(0, Number(step.gained) || 0);
  if (step.action === "include_unknown") {
    return {
      key: `${step.key}:${step.action}`,
      text: `放宽「${label}」：多出 ${gained} 人。他们只是没填${label}，不是明确不符合的人。`,
    };
  }
  if (step.action === "lower") {
    return {
      key: `${step.key}:${step.action}:${step.to_value ?? ""}`,
      text: `${label}从 ${followerText(step.from_value)} 降到 ${followerText(step.to_value)}：多出 ${gained} 人。`,
    };
  }
  return {
    key: `${step.key}:${step.action}`,
    text: `不再限制「${label}」：多出 ${gained} 人。这一条本来就是系统替你推断的，不是你自己选的。`,
  };
}

function protectedNoteOf(keys: string[] | undefined): string {
  const labels = Array.from(
    new Set((keys || []).map((key) => PROTECTED_LABELS[key]).filter((label): label is string => Boolean(label))),
  );
  if (!labels.length) return "";
  return `${labels.join("、")}属于合格线，任何时候都不会被自动放宽。`;
}

function sourceNoteOf(source: AutoRelaxPayload["advice_source"]): string {
  return source === "model"
    ? "这次的搜索条件是系统读你的描述后给的。"
    : "这次的搜索条件是按固定规则给的，没能读懂你的描述。";
}

function relaxedNames(steps: AutoRelaxStepPayload[]): string {
  const names = Array.from(new Set(steps.map((step) => labelOf(step.key)).filter(Boolean)));
  return names.map((name) => `「${name}」`).join("、");
}

/** 松项那半边的话:标题 + 语气。加项那半边由 `addedHeadlineOf` 管,两边互不吃掉对方。 */
function relaxHeadline(
  status: AutoRelaxStatus | undefined,
  applied: AutoRelaxStepPayload[],
  counts: { baseline: number; final: number },
  scene: { added: number; dropped: number },
): { tone: AutoRelaxView["tone"]; headline: string } {
  if (status === "disabled") {
    // 「已按你原来的条件搜索」只有在系统一条都没替他加的时候才是真话。
    // 加项还在生效却这么说,等于用一句好听的话把加筛选盖过去。
    return {
      tone: "plain",
      headline: scene.dropped
        ? "已按你自己的条件搜索：系统加的条件都去掉了，也没有做任何放宽。"
        : scene.added
          ? "这次没有自动放宽；但系统仍替你加了下面这些你没说过的条件。"
          : "已按你原来的条件搜索，没有自动放宽。",
    };
  }
  if (status === "unavailable") {
    // 真实发生的事:估不出人数就直接不放宽了。别再说「条件按原样执行了」——
    // 系统加的条件可能仍在生效,那句话与实际不符(2026-08-26 复核纠偏)。
    return {
      tone: "plain",
      headline: scene.added
        ? "这次没能预先估算能出多少人，所以一格都没有自动放宽；系统替你加的条件仍在生效。"
        : "这次没能预先估算能出多少人，所以一格都没有自动放宽。",
    };
  }
  if (status === "relaxed" && applied.length) {
    return {
      tone: "relaxed",
      headline: `本来只能出 ${counts.baseline} 人，自动放宽了${relaxedNames(applied)}之后能出 ${counts.final} 人。`,
    };
  }
  if (status === "short" && !applied.length) {
    return {
      tone: "short",
      headline: counts.final
        ? `这个条件下库里只有 ${counts.final} 人。你选的条件系统一格都没动。`
        : "这个条件下库里就是没有人。你选的条件系统一格都没动。",
    };
  }
  if (status === "short") {
    return {
      tone: "short",
      headline: counts.final
        ? `自动放宽了${relaxedNames(applied)}，也只能出 ${counts.final} 人——这个条件下库里就这么多人。`
        : `自动放宽了${relaxedNames(applied)}，还是一个人都没有——这个条件下库里就是没有人。`,
    };
  }
  // not_needed(或认不出的状态):放宽这半边没发生任何事,如实说没放宽。
  return { tone: "plain", headline: "这次的条件够用，没有自动放宽。" };
}

/**
 * 把后端台账翻成界面上的话。看不懂 / 真的没发生任何事的一律返回 null(不制造噪音)。
 *
 * 「发生过事」= 系统松过操作员的条件(`applied`)**或**替他加过条件(`added` / `added_dropped`)。
 * 加项即使一条都没松过也必须显示 —— 静默加条件正是本轮要根治的病。
 */
export function deriveAutoRelaxView(payload: unknown): AutoRelaxView | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as AutoRelaxPayload;
  if (record.schema && record.schema !== AUTO_RELAX_SCHEMA) return null;
  const status = record.status;
  if (!KNOWN_STATUSES.includes(String(status || ""))) return null;
  // 只有这两档的定义里真的松过;其余档即使台账里带着旧的松绑明细也不显示,
  // 免得界面说了一件这次没发生的事。
  const relaxHappened = status === "relaxed" || status === "short";
  const applied = relaxHappened && Array.isArray(record.applied) ? record.applied : [];
  const dropped = Array.isArray(record.added_dropped) ? record.added_dropped : [];
  const addedLines = (Array.isArray(record.added) ? record.added : [])
    .map(addedLine)
    .filter((line): line is AutoRelaxAddedLine => Boolean(line));
  const droppedNote = droppedNoteOf(dropped);
  const changed = applied.length > 0 || addedLines.length > 0 || Boolean(droppedNote);
  // 「人不够」「关掉了自动放宽」「估不出人数」这三档即使一格都没动也要说话(如实交代现状);
  // 其余情况真的什么都没发生,就不占版面。
  if (!changed && (status === "not_needed" || status === "relaxed")) return null;

  const { tone, headline } = relaxHeadline(
    status,
    applied,
    { baseline: Number(record.baseline_count ?? 0), final: Number(record.final_count ?? 0) },
    { added: addedLines.length, dropped: dropped.length },
  );
  // 还原按钮在**任何**状态下都给得出:只要系统动过手脚(松了**或**加了),就能一键回到
  // 操作员自己的条件;已经回到他自己条件的那一档给反向按钮,让他也能一键回到系统建议。
  // 本前端送 `auto_relax:false` 时必定同时送 `auto_filters:false`,所以 disabled 与
  // 「加项已去掉」总是同时出现;两者分开判是为了老客户端 / 直接调接口的台账也不说假话。
  const restored = status === "disabled" || Boolean(droppedNote);
  return {
    tone,
    headline,
    lines: applied.map(stepLine).filter((line): line is AutoRelaxLine => Boolean(line)),
    addedHeadline: addedHeadlineOf(addedLines),
    addedLines,
    droppedNote,
    removeLabel: "去掉这条",
    scopeNote: String(record.scope_note || ""),
    protectedNote: protectedNoteOf(record.protected_untouched),
    sourceNote: sourceNoteOf(record.advice_source),
    restoreLabel: restored
      ? (droppedNote ? "恢复系统建议" : "恢复自动放宽")
      : changed
        ? "改回我的条件"
        : null,
  };
}

/** 从整份搜索响应里挖出台账。响应形状历经多次投影,这里逐层容错。 */
export function autoRelaxFromResponse(response: unknown): AutoRelaxPayload | null {
  if (!response || typeof response !== "object") return null;
  const root = response as Record<string, unknown>;
  const direct = root.auto_relax;
  if (direct && typeof direct === "object") return direct as AutoRelaxPayload;
  const result = root.result;
  if (result && typeof result === "object") {
    const nested = (result as Record<string, unknown>).auto_relax;
    if (nested && typeof nested === "object") return nested as AutoRelaxPayload;
  }
  return null;
}

/** 一次搜索请求里与「加 / 松」有关的三个开关。缺一不可:少一个就回不到操作员的条件。 */
export interface AutoRelaxRequestParams {
  autoRelax: boolean;
  autoFilters: boolean;
  droppedAutoFilters: string[];
}

export interface AutoRelaxControl {
  view: AutoRelaxView | null;
  optOut: boolean;
  /** 操作员已经逐条点掉的系统加项(键名)。界面据此把那颗按钮置灰。 */
  droppedKeys: string[];
  /** 请求体里的 `auto_relax`。false = 这次一格都不许放宽。 */
  enabledForRequest: () => boolean;
  /** 请求体里的三个开关。`optOut` 打开时既不放宽、也不采纳系统加的条件。 */
  requestParams: () => AutoRelaxRequestParams;
  /** 一键改回操作员自己的条件(或反向恢复系统建议),随后由调用方重跑搜索。 */
  toggleOptOut: () => void;
  /** 单独去掉系统加的某一条,随后由调用方重跑搜索。 */
  removeAdded: (key: string) => void;
}

/**
 * 把「读台账」「一键还原」「逐条去掉系统加的条件」收在一个 hook 里,面板侧只加两行。
 *
 * 状态用 ref 镜像:按钮要在同一个事件里 setState + 立刻重跑搜索,
 * 只靠 state 的话请求体会读到上一轮的旧值(把「改回我的条件」变成一次假动作)。
 *
 * 「改回我的条件」= `auto_relax:false` **且** `auto_filters:false`。上一版只送前者,
 * 系统推断出来的国家 / 语言 / 垂类照样被硬加上去 —— 按钮按了也回不去。
 */
export function useAutoRelaxControl(response: unknown): AutoRelaxControl {
  const [optOut, setOptOut] = useState(false);
  const [dropped, setDropped] = useState<string[]>([]);
  const optOutRef = useRef(false);
  const droppedRef = useRef<string[]>([]);
  const requestParams = (): AutoRelaxRequestParams => ({
    autoRelax: !optOutRef.current,
    autoFilters: !optOutRef.current,
    droppedAutoFilters: [...droppedRef.current],
  });
  return {
    view: deriveAutoRelaxView(autoRelaxFromResponse(response)),
    optOut,
    droppedKeys: dropped,
    enabledForRequest: () => !optOutRef.current,
    requestParams,
    toggleOptOut: () => {
      optOutRef.current = !optOutRef.current;
      // 回到系统建议时,逐条点掉的那几项也一并恢复 —— 否则「恢复」只恢复一半。
      droppedRef.current = [];
      setOptOut(optOutRef.current);
      setDropped([]);
    },
    removeAdded: (key: string) => {
      const next = String(key || "").trim();
      if (!next || droppedRef.current.includes(next)) return;
      droppedRef.current = [...droppedRef.current, next];
      setDropped(droppedRef.current);
    },
  };
}
