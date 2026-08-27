// 「语言」这一格必须说清楚是谁说的:
//   · 自报 —— 他在平台资料里自己填的;
//   · 推断 —— 平台资料没写,是照他自己发的个人简介 / 作品标题倒推出来的;
//   · 未知 —— 我们这里没有他的语言,或者到底是谁说的我们判不出来。
//
// 红线:推断出来的值绝不冒充自报值 —— 门面上必须看得出区别,并且看得到依据。
// 推断不出来的人显示「未知」,不许留空让人以为是没数据。
// 反过来同样是红线:**他没填,就不许说他填了**。平台把「没填」写成 "Unknown" / "N/A"
// 这类占位词时,那不是一门语言,更不是一句自报声明 —— 一律当没有值,那个人回「未知」档。
//
// **门面是第二道防线,不是服务端的传声筒。** 服务端会给一张三态明牌,但明牌也会标错
// (值明明取自推断那一列,标签却写着「自报」)。所以明牌与手上的证据打架时:
//   · 证据直接说得出「这个值哪来的」(值取自推断形状的键)—— 以证据为准,标「推断」;
//   · 两边各执一词、又没有第三份材料可判 —— 落「未知」,不显示值。
// 两条路都绝不会因为服务端标错一次,门面就跟着替他伪造一句他没说过的自报声明。
//
// 同一条线还管文案:**不许替系统做未经验证的事实声明**。「未知」那一档只说得出
// 「我们这里没有」,说不出「推不出来」—— 有没有试过推,门面根本无从知道。
//
// 本文件只做「读服务端给的口径 + 说人话」,不在浏览器里判语言,也不放宽任何标准。

export type LanguageOrigin = "self_reported" | "inferred" | "unknown";

export type LanguageProvenance = {
  origin: LanguageOrigin;
  /** 归一后的语言代码(小写、去地区后缀、去重)。 */
  codes: string[];
  /** 格内主文案:有值时是大写代码,推断不出来时是「未知」。 */
  displayLabel: string;
  /** 说人话的语言名,给悬停整句和详情页用。 */
  nameLabel: string;
  /** 「自报」/「推断」/「未知」。 */
  originLabel: string;
  /** 推断依据:「个人简介」/「作品标题」/「个人简介和作品标题」;说不出依据时为空。 */
  basisLabel: string;
  /** 他自己填的那组值(可能为空)。 */
  selfReportedCodes: string[];
  /** 我们推断出来的那组值(可能为空)。 */
  inferredCodes: string[];
  /** 他自己填的和我们推断的对不上时的说明;一致或缺一边时为空。 */
  divergenceLabel: string;
  /** 详情页值底下那行小字(「他自己填的」/「推断 · 依据作品标题」/「我们这里没有语言信息」)。 */
  noteLabel: string;
  /** 悬停整句。 */
  title: string;
};

type Row = Record<string, unknown>;

// 与筛选面板 SMART_KOL_LANGUAGE_OPTIONS 同一套口径。此处独立成表是为了不把
// 搜索面板的组件拖进 KOL 详情抽屉的打包分片(分片余量已经很紧)。
// 两张表必须一致 —— LanguageProvenance.test.tsx 里有一致性断言兜着。
const LANGUAGE_NAMES: Readonly<Record<string, string>> = Object.freeze({
  en: "英语", ja: "日语", ko: "韩语", de: "德语", fr: "法语", es: "西语",
  pt: "葡语", it: "意语", ru: "俄语", th: "泰语", vi: "越语", id: "印尼语",
  ms: "马来语", nl: "荷兰语", pl: "波兰语", sv: "瑞典语", tr: "土耳其语",
  zh: "中文", ar: "阿语",
});

// 平台 / 抓取器在「这一格他没填」的位置塞进来的占位词。它们**不是语言**:
// 照单收下会让一个什么都没填的人在门面上显示成「Unknown」,还被标成「自报」——
// 等于我们替他伪造了一句他从没说过的话。一律当没有值处理,他回「未知」档。
//
// 收词只收不会跟真语言撞车的:`no`(挪威语)这种绝不列入,宁可把一个占位词放过去,
// 也不能把真填了挪威语的人抹成「未知」。`na` 与既有 EMPTY_CANDIDATE_TEXT 口径一致地
// 当占位词收 —— 落到「未知」是保守方向,与新鲜闸「判不出=未知,不是不合格」同口径。
const NON_LANGUAGE_TOKENS: ReadonlySet<string> = new Set([
  "unknown", "unspecified", "undefined", "undetermined", "unavailable",
  "not specified", "not set", "not available", "no data",
  "none", "null", "nil", "n/a", "n a", "na", "other", "others",
  "auto", "default", "?", "??",
  // ISO 639 里专门表示「没有语言内容 / 判不出 / 不止一种」的编号,同样不是某一门语言。
  "und", "zxx", "mis", "mul",
  "未知", "无", "未填写", "未提供", "不详", "暂无",
]);

// 服务端的三态明牌(qualification_evidence.language.origin)。有它就照它说的算。
const ORIGIN_VALUES: Readonly<Record<string, LanguageOrigin>> = Object.freeze({
  self_reported: "self_reported",
  inferred: "inferred",
  unknown: "unknown",
});

// 没有明牌时的兜底:来源串里出现这些片段,就说明值是从公开内容里倒推的,不是他自己填的。
const INFERRED_SOURCE_MARKERS = [
  "infer", "detect", "estimat", "derive", "guess",
  "public_content", "content_text", "from_content", "content_inference",
];

// 依据字段名 → 说人话。个人简介与作品标题分开说,操作员才知道我们看的是什么。
const BIO_FIELD_MARKERS = ["bio", "description", "about", "profile_text", "intro", "summary"];
const TITLE_FIELD_MARKERS = ["title", "video", "sample", "caption", "post"];

function text(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function row(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};
}

/** 只认字符串/数字标量,防止把嵌套对象 stringify 成 "[object Object]" 当语言代码。 */
function scalarList(value: unknown): string[] {
  const raw = Array.isArray(value) ? value : [value];
  return raw
    .filter((entry) => typeof entry === "string" || typeof entry === "number")
    .map((entry) => text(entry).toLowerCase())
    .filter(Boolean);
}

// 连接符一家人:ASCII 连字符、各路破折号(连接号 / 短破折 / 长破折 / 全角减号)与下划线。
// 只认 ASCII `-` 是不够的 —— 长破折号 U+2014「—」**恰恰是这块门面自己原来用的空位占位符**,
// 漏认它,一个纯粹表示「这里没有」的横杠就会被当成一门语言,还被标成「他自己填的」。
const SEPARATOR_CHARS = "\\s_\\-\\u2010-\\u2015\\u2212\\uFF0D";
const SEPARATOR_RUN = new RegExp(`[${SEPARATOR_CHARS}]+`, "g");
// 切地区后缀(`zh-CN`)用:不吃空白,免得把 `English (US)` 这种整词从中间截断。
const REGION_SEPARATOR = new RegExp(`[_\\-\\u2010-\\u2015\\u2212\\uFF0D]`);

/** `not_specified` / `NOT-SPECIFIED` / `not specified` 是同一个占位词,归一后再比。 */
function placeholderKey(token: string): string {
  return token.replace(SEPARATOR_RUN, " ").trim();
}

/**
 * 空串、光剩连接符的横杠(`—` / `-` / `___`)、或平台拿来表示「没填」的占位词
 * —— 三者都是「这里没有值」,不是一门语言。
 */
function isNonLanguageToken(token: string): boolean {
  const key = placeholderKey(token);
  return !key || NON_LANGUAGE_TOKENS.has(key);
}

/**
 * 取语言代码。占位词在**切地区后缀之前和之后各拦一道**:
 * 之前拦掉 `not_specified` 这种带下划线的整词,之后拦掉 `unknown-US` 这种切完才现形的。
 * 拦下来的结果是「这一路没有值」,由上游落回「未知」档 —— 绝不变成一个显示得出来的值。
 */
function languageCodes(value: unknown): string[] {
  return scalarList(value)
    .filter((entry) => !isNonLanguageToken(entry))
    .map((entry) => entry.split(REGION_SEPARATOR)[0])
    .filter((code) => !isNonLanguageToken(code));
}

function isInferredSource(source: string): boolean {
  return INFERRED_SOURCE_MARKERS.some((marker) => source.includes(marker));
}

function basisLabelOf(fields: readonly string[]): string {
  const hasBio = fields.some((field) => BIO_FIELD_MARKERS.some((marker) => field.includes(marker)));
  const hasTitle = fields.some((field) => TITLE_FIELD_MARKERS.some((marker) => field.includes(marker)));
  if (hasBio && hasTitle) return "个人简介和作品标题";
  if (hasBio) return "个人简介";
  if (hasTitle) return "作品标题";
  return "";
}

type Picked = { codes: string[]; fromInferredKey: boolean };

/** 自报形状的键优先;只有推断形状的键有值时,标记它来自推断键。 */
function pickCodes(current: Picked, declared: unknown[], inferred: unknown[]): Picked {
  if (current.codes.length) return current;
  const declaredCodes = declared.flatMap(languageCodes);
  if (declaredCodes.length) return { codes: declaredCodes, fromInferredKey: false };
  const inferredCodes = inferred.flatMap(languageCodes);
  if (inferredCodes.length) return { codes: inferredCodes, fromInferredKey: true };
  return current;
}

/** 布尔当标记读,字符串/数组当语言值读 —— 同名键两种用法都得认。 */
function flagOf(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function codeOf(value: unknown): unknown {
  return typeof value === "boolean" ? null : value;
}

/** 两三个字母的是语言代码,全大写好认;整词(平台偶尔存 "english")就别喊,首字母大写即可。 */
function codeLabelOf(code: string): string {
  return code.length <= 3 ? code.toUpperCase() : code.charAt(0).toUpperCase() + code.slice(1);
}

function nameList(codes: readonly string[]): string {
  return codes.map((code) => LANGUAGE_NAMES[code] || codeLabelOf(code)).join("、");
}

function sameCodes(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((code) => right.includes(code));
}

type OriginSignals = {
  /** 手上有没有一个显示得出来的语言值(占位词已经滤干净)。 */
  hasValue: boolean;
  /** 服务端三态明牌。 */
  declaredOrigin?: LanguageOrigin;
  /** 显示的这个值是从推断形状的键上取来的 —— 关于「值哪来的」的直接证据。 */
  valueFromInferredKey: boolean;
  /** 记录里明写着 inferred=true。 */
  inferredFlag: boolean;
  /** 语言来源串是推断口径。 */
  inferredSource: boolean;
};

/** 判不出来时同时说清楚是「压根没有值」还是「有值但认不出是谁说的」。 */
type OriginDecision = { origin: LanguageOrigin; conflict: boolean };

/**
 * 第二道防线:服务端明牌与本地证据打架时不照单全收。
 *
 * 明牌说「推断」——照收。这一头是安全方向:标成推断至多是少替他领了一句功劳,
 * 绝不会凭空替他伪造一句自报声明。
 *
 * 明牌说「自报」而证据说是推断出来的,分两种:
 *  1. 值本身取自推断形状的键(自报那一格是空的 / 是占位词,由推断值顶上来的)——
 *     这是关于「这个值哪来的」的**直接**证据,比一句标签硬。以证据为准,标「推断」。
 *  2. 只有旁证(记录里另写着 inferred=true、或来源串是推断口径)与明牌顶牛 ——
 *     两句话不能同时为真,而我们没有第三份材料能判谁对。这时候倒向哪一边都是替系统
 *     说一句没验证过的话:落「未知」,值也不显示。
 *
 * 明牌缺席(会话回放、联网车道的旧结果)才退回原来的形状判断。
 */
function decideOrigin(signals: OriginSignals): OriginDecision {
  const { hasValue, declaredOrigin, valueFromInferredKey, inferredFlag, inferredSource } = signals;
  if (!hasValue) return { origin: "unknown", conflict: false };
  const evidenceSaysInferred = valueFromInferredKey || inferredFlag || inferredSource;
  if (declaredOrigin === "inferred") return { origin: "inferred", conflict: false };
  if (declaredOrigin === "self_reported") {
    if (valueFromInferredKey) return { origin: "inferred", conflict: true };
    if (evidenceSaysInferred) return { origin: "unknown", conflict: true };
    return { origin: "self_reported", conflict: false };
  }
  return { origin: evidenceSaysInferred ? "inferred" : "self_reported", conflict: false };
}

// 「未知」那一档的文案。**只陈述查得到的事实**:我们这里没有拿到值。
// 不许写「我们也没有足够的文字可以推断出来」—— 推断列有没有被读到、推断有没有跑过,
// 门面一概不知道,那句话是替系统做一个未经验证的事实声明。
const UNKNOWN_TITLE = "我们这里没有这个人的语言：平台资料里没有可用的值，也没有拿到推断出来的值。";
const UNKNOWN_NOTE = "我们这里没有语言信息";
// 有值,但两份记录对「谁说的」各执一词。同样只陈述事实:对不上,所以不显示。
const CONFLICT_TITLE = "这个人的语言暂时不显示：手上两份记录对「这个值是哪来的」说法不一致，我们无法确定该信哪一份。";
const CONFLICT_NOTE = "两份记录对不上，暂不显示";

/**
 * 从服务端给的若干层记录里读出「语言是什么 + 谁说的 + 凭什么」。
 *
 * 分两类读,互不串味:
 *  · 嵌套的语言证据对象(硬闸的 language 格、facet_evidence.language、language_evidence)
 *    ——读它自己的 origin/value/values/source/inferred/evidence_fields;
 *  · 平铺的条目记录 ——只读 language_* 前缀的键。条目上的裸 `source` 是「这条结果
 *    哪来的」(例如联网发现车道),不是语言来源,读了会张冠李戴。
 *
 * 服务端硬闸已经给出三态明牌 `origin`,有明牌就照明牌;没有明牌的旧结果(会话回放、
 * 联网车道)才退回来源串与推断键的形状判断。
 */
export function resolveLanguageProvenance(records: readonly unknown[]): LanguageProvenance {
  const rows = records.map(row);
  const nested: Row[] = [];
  rows.forEach((entry) => {
    nested.push(row(row(entry.facet_evidence).language));
    nested.push(row(entry.language_evidence));
    nested.push(row(entry.language));
  });

  let picked: Picked = { codes: [], fromInferredKey: false };
  const declaredOrigins: LanguageOrigin[] = [];
  const sources: string[] = [];
  const flags: boolean[] = [];
  const basisFields: string[] = [];
  const selfCodes: string[] = [];
  const inferredCodes: string[] = [];

  const noteOrigin = (value: unknown): string => {
    const token = text(value).toLowerCase();
    const declared = ORIGIN_VALUES[token];
    if (declared) {
      declaredOrigins.push(declared);
      return "";
    }
    return token;
  };

  nested.forEach((entry) => {
    picked = pickCodes(picked, [entry.values, entry.value], [entry.inferred_value, entry.inferred_values]);
    const originToken = noteOrigin(entry.origin);
    const source = text(entry.source).toLowerCase() || originToken;
    if (source) sources.push(source);
    const flag = flagOf(entry.inferred);
    if (flag !== null) flags.push(flag);
    selfCodes.push(...languageCodes(entry.self_reported_values));
    inferredCodes.push(...languageCodes(entry.inferred_values));
    basisFields.push(...scalarList(entry.evidence_fields), ...scalarList(entry.basis), ...scalarList(entry.inferred_from));
  });

  rows.forEach((entry) => {
    picked = pickCodes(
      picked,
      [entry.language, entry.content_language],
      [codeOf(entry.language_inferred), entry.inferred_language],
    );
    const originToken = noteOrigin(entry.language_origin);
    const source = text(entry.language_source).toLowerCase() || originToken;
    if (source) sources.push(source);
    const flag = flagOf(entry.language_inferred);
    if (flag !== null) flags.push(flag);
    selfCodes.push(...languageCodes(entry.language));
    inferredCodes.push(...languageCodes(codeOf(entry.language_inferred)), ...languageCodes(entry.inferred_language));
    basisFields.push(
      // 落库列 language_inferred_source 装的是「依据哪段文字」(bio / video_titles),
      // 不是来源串 —— 当依据读,不当来源读。
      ...scalarList(entry.language_inferred_source),
      ...scalarList(entry.language_evidence_fields),
      ...scalarList(entry.language_basis),
      ...scalarList(entry.language_inferred_basis),
      ...scalarList(entry.language_inferred_from),
    );
  });

  const codes = Array.from(new Set(picked.codes));
  const selfReportedCodes = Array.from(new Set(selfCodes));
  const inferredValueCodes = Array.from(new Set(inferredCodes));
  const declaredOrigin = declaredOrigins.find((value) => value === "inferred")
    ?? declaredOrigins.find((value) => value === "self_reported");
  const decided = decideOrigin({
    hasValue: codes.length > 0,
    declaredOrigin,
    valueFromInferredKey: picked.fromInferredKey,
    inferredFlag: flags.includes(true),
    inferredSource: sources.some(isInferredSource),
  });
  const origin = decided.origin;
  const codeLabel = codes.map(codeLabelOf).join("/");
  const nameLabel = nameList(codes);
  const basisLabel = origin === "inferred" ? basisLabelOf(basisFields) : "";
  // 他自己填的和我们推断的对不上时如实说出来,不许悄悄抹平成一个值。
  //
  // 「他自己填的是……」这半句是**替他转述一句话**,所以只有在他确实填了东西时才许说:
  // `selfReportedCodes` 已经把占位词滤干净了,占位词进不来,这句话也就编不出来。
  const divergenceLabel = selfReportedCodes.length && inferredValueCodes.length
    && !sameCodes(selfReportedCodes, inferredValueCodes)
    ? `他自己填的是${nameList(selfReportedCodes)}，照他发的东西推断出来的是${nameList(inferredValueCodes)}。`
    : "";
  const shared = { codes, selfReportedCodes, inferredCodes: inferredValueCodes, divergenceLabel };

  if (origin === "unknown") {
    return {
      ...shared,
      // 说「未知」就得从头到尾都未知:这一档不许漏出任何一个具体语言值 ——
      // 包括来源打架被扣下的那个值,以及旁挂的自报 / 推断两组码。分歧那句话也一并噤声。
      codes: [],
      selfReportedCodes: [],
      inferredCodes: [],
      origin,
      displayLabel: "未知",
      nameLabel: "",
      originLabel: "未知",
      basisLabel: "",
      divergenceLabel: "",
      noteLabel: decided.conflict ? CONFLICT_NOTE : UNKNOWN_NOTE,
      title: decided.conflict ? CONFLICT_TITLE : UNKNOWN_TITLE,
    };
  }
  if (origin === "inferred") {
    return {
      ...shared,
      origin,
      displayLabel: codeLabel,
      nameLabel,
      originLabel: "推断",
      basisLabel,
      noteLabel: basisLabel ? `推断 · 依据${basisLabel}` : "推断 · 依据他发的内容",
      title: `${nameLabel} · 平台资料上没写语言，这是照他自己发的${basisLabel || "内容"}推断出来的，不是他自己填的。${divergenceLabel}`,
    };
  }
  return {
    ...shared,
    origin,
    displayLabel: codeLabel,
    nameLabel,
    originLabel: "自报",
    basisLabel: "",
    noteLabel: "他自己填的",
    title: `${nameLabel} · 他在平台资料里自己填的。${divergenceLabel}`,
  };
}

export type LanguageOriginCounts = { selfReported: number; inferred: number; unknown: number };

export function languageOriginCounts(values: readonly LanguageProvenance[]): LanguageOriginCounts {
  return {
    selfReported: values.filter((value) => value.origin === "self_reported").length,
    inferred: values.filter((value) => value.origin === "inferred").length,
    unknown: values.filter((value) => value.origin === "unknown").length,
  };
}

/** 统计条:只有真出现过推断或未知才值得占一格,全是自报时不喂噪音。 */
export function languageOriginSummaryLabel(counts: LanguageOriginCounts): string {
  if (!counts.inferred && !counts.unknown) return "";
  const parts = [`自报 ${counts.selfReported}`];
  if (counts.inferred) parts.push(`推断 ${counts.inferred}`);
  if (counts.unknown) parts.push(`未知 ${counts.unknown}`);
  return `语言 · ${parts.join(" · ")}`;
}
