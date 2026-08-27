// 「语言」这一格必须说清楚是谁说的。**四档,泾渭分明**:
//   · 自报 —— 他在平台资料里自己填的;
//   · 推断 —— 平台资料没写,是照他自己发的个人简介 / 作品标题倒推出来的;
//   · 来源不明 —— 资料里确实有这个值,但看不出是不是他自己填的;
//   · 未知 —— 我们这里没有拿到可用的值。
//
// ── 本文件在 2026-08-27 被**拆掉了推导** ────────────────────────────────────
//
// 归属是**服务端裁的**(backend/app/domains/kol/profile_recall_language_gate.py 的
// `resolve_candidate_language` / `language_gate_evidence`),那份裁决是唯一真源。
// 这里的职责从此只有一件:**把裁决渲染出来**,而不是拿原料再算一遍。
//
// 之前三轮复核每次都在补下一层判断,补一层就多一处能说错话的地方:占位词被当语言、
// 第四档不被识别、平铺行伪造自报声明、没明牌就兜底成自报、抓取器载荷与本地列同等对待、
// 没过门槛的推断值被升格。共同的病根不是「判断不够多」,是「前端在判断」。
//
// 三条不可退让的规矩:
//
//  1. **有裁决就照裁决渲染,一个字不改。** 裁决说 unknown,就是未知 —— 哪怕别的层
//     还躺着一个值,也不许拿它来「补全」裁决(那是拿原料翻案)。
//  2. **没有裁决时(旧数据、别的接口没带这些字段)绝不落「自报」。** 降级路径的出口
//     只有两个:手上有值就说「来源不明」(资料里有,看不出谁填的),没值就说「未知」。
//     **没有任何一条分支通向「自报」** —— 那正是三轮都没根治的那句假话。
//  3. **抓取器原始载荷不是我们的列。** `raw_platform_data` 及其嵌套 `raw` 是 provider
//     说的话,由它得来的值**至多**是「来源不明」,而且永远不参与「他自己填的是……」
//     那半句,也不算「我们推断的」。裁决只从本地记录里读,provider 载荷里的
//     `origin` 之类的键一概不当裁决看。
//
// 还有一条关于「没把握的那一票」:服务端把没过置信门槛的推断值旁挂在
// `inferred_values` 上,同时把 `origin` 判成 unknown、`values` 留空 —— 那是**它自己
// 不敢用**的一票。门面可以如实说「我们试过、把握不够」,但**不许算成推断档**,
// 也不许计进统计条的推断数。
//
// **这一条在两条路上都要成立。** 没有裁决时(旧数据、别的接口)门面读的是落库列,
// 那就必须连同一行的 `language_inferred_confidence` 一起读:只按键名判档、不读把握度,
// 等于把服务端「没敢用」的那一票在池子和抽屉里升格成结论。门槛取值与服务端同源
// (见 `MIN_INFERRED_CONFIDENCE`),门面不自立一套宽一点的标准。
//
// 文案红线照旧:不许替系统做未经验证的事实声明。「什么都没有」那一档只说得出
// 「我们这里没有」,说不出「推不出来」;只有服务端亲口说了它试过(旁挂了一票),
// 才许说「试过、把握不够」。
//
// 本文件不在浏览器里判语言,也不放宽任何标准 —— 门槛(粉丝 / 新鲜度 / 器材证据 /
// 产品锚 / 置信门槛)全在服务端,这里一格没动。

export type LanguageOrigin = "self_reported" | "inferred" | "projected" | "unknown";

export type LanguageProvenance = {
  origin: LanguageOrigin;
  /** 归一后的语言代码(小写、去地区后缀、去重)。 */
  codes: string[];
  /** 格内主文案:有值时是大写代码,没有可显示的值时是「未知」。 */
  displayLabel: string;
  /** 说人话的语言名,给悬停整句和详情页用。 */
  nameLabel: string;
  /** 「自报」/「推断」/「来源不明」/「未知」。 */
  originLabel: string;
  /** 推断依据:「个人简介」/「作品标题」/「个人简介和作品标题」;说不出依据时为空。 */
  basisLabel: string;
  /** 服务端**指名道姓**说是他自己填的那组值(它没说就是空,门面不补)。 */
  selfReportedCodes: string[];
  /** 服务端说是我们推断出来的那组值(可能为空)。 */
  inferredCodes: string[];
  /** 他自己填的和我们推断的对不上时的说明;一致或缺一边时为空。 */
  divergenceLabel: string;
  /** 详情页值底下那行小字。 */
  noteLabel: string;
  /** 悬停整句。 */
  title: string;
  /** 这一格是照服务端裁决渲染的,还是没有裁决、只是在如实描述手上的原料。 */
  hasServerVerdict: boolean;
  /**
   * 服务端试着从他发的内容里判断过,但它自己觉得把握不够,没把这一票当结论
   * (裁决 `origin=unknown` + `values=[]` + 旁挂着 `inferred_values`)。
   * **这一档算「未知」,不算「推断」** —— 统计条里也一样。
   */
  inferenceWithheld: boolean;
};

/**
 * 喂给本函数的记录**按可信级别分开传**,不许混成一锅:
 *
 *  · `local`    —— 我们自己的东西:服务端裁决块(`qualification_evidence.language`)、
 *                  本地库列(`vkpi_kol_pool.language` / `language_inferred` …)。
 *                  **裁决只从这一路读。**
 *  · `provider` —— 抓取器原始载荷(`raw_platform_data` 及其嵌套 `raw`)。provider 说的话,
 *                  由它得来的值至多是「来源不明」,永远不算自报、也不算我们推断的。
 *
 * 传数组 = 整批都当 `local`(既有调用方的写法,语义不变)。
 */
export type LanguageRecordSet = {
  local?: readonly unknown[];
  provider?: readonly unknown[];
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
// 照单收下会让一个什么都没填的人在门面上显示成「Unknown」。一律当没有值处理。
//
// 这是**显示卫生**,不是归属判定:它只回答「这串字符是不是一门语言」,
// 不回答「谁说的」。服务端不做这一层过滤(它按列取值),所以门面必须做 ——
// 否则裁决里一个占位词会被原样显示成一个语言值。
//
// 收词只收不会跟真语言撞车的:`no`(挪威语)这种绝不列入。
const NON_LANGUAGE_TOKENS: ReadonlySet<string> = new Set([
  "unknown", "unspecified", "undefined", "undetermined", "unavailable",
  "not specified", "not set", "not available", "no data",
  "none", "null", "nil", "n/a", "n a", "na", "other", "others",
  "auto", "default", "?", "??",
  // ISO 639 里专门表示「没有语言内容 / 判不出 / 不止一种」的编号,同样不是某一门语言。
  "und", "zxx", "mis", "mul",
  "未知", "无", "未填写", "未提供", "不详", "暂无",
]);

// 服务端的四档裁决(qualification_evidence.language.origin)。四档必须与
// backend/app/domains/kol/profile_recall_language_gate.py 的 ORIGIN_* 一一对上。
// **认不出的档位一律不许退化成「自报」**(见 verdictOrigin)。
const ORIGIN_VALUES: Readonly<Record<string, LanguageOrigin>> = Object.freeze({
  self_reported: "self_reported",
  inferred: "inferred",
  projected: "projected",
  unknown: "unknown",
});

// 推断值够不够格被当成结论,由**把握度档位**说了算 —— 与后端
// backend/app/domains/kol/profile_recall_language_gate.py 的 `MIN_INFERRED_CONFIDENCE`
// 同一个门槛(2026-08-26 从 low 抬到 medium)。迁移 305 把把握度存成档位文字
// (high / medium / low),不是小数。
const CONFIDENCE_ORDER: readonly string[] = ["low", "medium", "high"];
const MIN_INFERRED_CONFIDENCE = "medium";
// 落库列 / 门面键两种拼法都认,免得两条车道各写各的、在中间对不上。
const CONFIDENCE_KEYS = ["language_inferred_confidence", "inferred_language_confidence"];

/**
 * 这一票推断的把握度够不够门槛。
 *
 * **读不出档位 = 证不出达标 = 不放行**,与后端 `meets_confidence_floor` 同一个保守方向。
 * 旧布局没有这一列时后端在同一行上判的就是「未知」;门面这里若照收,墙上会写着
 * 「推断 KO」而后端把这个人算成未知 —— 同一行数据两张嘴。
 */
function meetsConfidenceFloor(value: unknown): boolean {
  return CONFIDENCE_ORDER.indexOf(text(value).toLowerCase())
    >= CONFIDENCE_ORDER.indexOf(MIN_INFERRED_CONFIDENCE);
}

// 依据字段名 → 说人话。个人简介与作品标题分开说,操作员才知道我们看的是什么。
const BIO_FIELD_MARKERS = ["bio", "description", "about", "profile_text", "intro", "summary"];
const TITLE_FIELD_MARKERS = ["title", "video", "sample", "caption", "post"];

function text(value: unknown): string {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function row(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};
}

function uniq(codes: readonly string[]): string[] {
  return Array.from(new Set(codes));
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
// 只认 ASCII `-` 是不够的 —— 长破折号 U+2014「—」**恰恰是这块门面自己原来用的空位占位符**。
const SEPARATOR_CHARS = "\\s_\\-\\u2010-\\u2015\\u2212\\uFF0D";
const SEPARATOR_RUN = new RegExp(`[${SEPARATOR_CHARS}]+`, "g");
// 切地区后缀(`zh-CN`)用:不吃空白,免得把 `English (US)` 这种整词从中间截断。
const REGION_SEPARATOR = new RegExp(`[_\\-\\u2010-\\u2015\\u2212\\uFF0D]`);

/** `not_specified` / `NOT-SPECIFIED` / `not specified` 是同一个占位词,归一后再比。 */
function placeholderKey(token: string): string {
  return token.replace(SEPARATOR_RUN, " ").trim();
}

/** 空串、光剩连接符的横杠、或平台拿来表示「没填」的占位词 —— 都是「这里没有值」。 */
function isNonLanguageToken(token: string): boolean {
  const key = placeholderKey(token);
  return !key || NON_LANGUAGE_TOKENS.has(key);
}

/**
 * 取语言代码。占位词在**切地区后缀之前和之后各拦一道**:
 * 之前拦掉 `not_specified` 这种带下划线的整词,之后拦掉 `unknown-US` 这种切完才现形的。
 */
function languageCodes(...values: readonly unknown[]): string[] {
  return values
    .flatMap(scalarList)
    .filter((entry) => !isNonLanguageToken(entry))
    .map((entry) => entry.split(REGION_SEPARATOR)[0])
    .filter((code) => !isNonLanguageToken(code));
}

function basisLabelOf(fields: readonly string[]): string {
  const hasBio = fields.some((field) => BIO_FIELD_MARKERS.some((marker) => field.includes(marker)));
  const hasTitle = fields.some((field) => TITLE_FIELD_MARKERS.some((marker) => field.includes(marker)));
  if (hasBio && hasTitle) return "个人简介和作品标题";
  if (hasBio) return "个人简介";
  if (hasTitle) return "作品标题";
  return "";
}

/** 布尔当标记读,字符串/数组当语言值读 —— 同名键两种用法都得认。 */
function codeOf(value: unknown): unknown {
  return typeof value === "boolean" ? null : value;
}

/** 两三个字母的是语言代码,全大写好认;整词(平台偶尔存 "english")首字母大写即可。 */
function codeLabelOf(code: string): string {
  return code.length <= 3 ? code.toUpperCase() : code.charAt(0).toUpperCase() + code.slice(1);
}

function nameList(codes: readonly string[]): string {
  return codes.map((code) => LANGUAGE_NAMES[code] || codeLabelOf(code)).join("、");
}

function sameCodes(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((code) => right.includes(code));
}

// ── 服务端裁决 ────────────────────────────────────────────────────────────────

type Verdict = {
  origin: LanguageOrigin;
  codes: string[];
  selfReportedCodes: string[];
  inferredCodes: string[];
  basisFields: string[];
  withheld: boolean;
};

/**
 * 裁决块只可能长在这四处 —— `language_gate_evidence()` 与 `adapt_language()` 的落点。
 * **平铺行不是裁决块**:行上的 `language_source` / `language_origin` 是原料上的记号,
 * 不是判决书,读它等于门面自己在判。
 *
 * `qualification_evidence.language`(2026-08-27 补):搜索候选走
 * `profile_discovery_candidates.py:500` 这条路时,裁决落在这个键下。
 * 漏读它不会说错话(降级路径的三个出口都不是「自报」),但会**白白丢掉一份已经算好的裁决**,
 * 退回自己推导 —— 而「别再自己推导」正是这一轮改造的全部意义。
 */
function verdictBlocks(entry: Row): Row[] {
  return [
    row(entry.language),
    row(row(entry.facet_evidence).language),
    row(entry.language_evidence),
    row(row(entry.qualification_evidence).language),
  ];
}

/** 一块记录够不够格叫「裁决」:服务端亲手写的那两个明牌,有一个就算。 */
function isVerdict(block: Row): boolean {
  return typeof block.origin === "string" || typeof block.self_reported === "boolean";
}

/**
 * 裁决 -> 展示档。**只做两件事**:把四档原样接住;把占位词滤掉。
 *
 * 认不出的档位(拼写漂移 / 将来新加的档)一律落「来源不明」,**绝不退化成「自报」**。
 * `origin` 说自报、而同一块里的布尔明牌 `self_reported` 明写着 false 时同样落「来源不明」
 * —— 这不是门面在判归属,是裁决自己前后不一,门面照它更保守的那一句走。
 */
function verdictOrigin(block: Row, codes: readonly string[]): LanguageOrigin {
  if (!codes.length) return "unknown";
  const declared = ORIGIN_VALUES[text(block.origin).toLowerCase()];
  if (declared === "self_reported") return block.self_reported === false ? "projected" : "self_reported";
  return declared || "projected";
}

function readVerdict(block: Row): Verdict {
  const declared = ORIGIN_VALUES[text(block.origin).toLowerCase()];
  const codes = uniq(languageCodes(block.values, block.value));
  const inferredCodes = uniq(languageCodes(block.inferred_values, block.inferred_value));
  // 服务端试过、但它自己没敢用的那一票:裁决判 unknown、`values` 留空,
  // 旁边却挂着一个推断值(或明写着卡在门槛下)。**这一档算未知,不算推断。**
  const withheld = declared === "unknown" && !codes.length
    && (inferredCodes.length > 0 || text(block.inference_below_floor) !== "");
  return {
    origin: verdictOrigin(block, codes),
    codes,
    selfReportedCodes: uniq(languageCodes(block.self_reported_values)),
    inferredCodes,
    basisFields: [
      ...scalarList(block.evidence_fields), ...scalarList(block.basis),
      ...scalarList(block.inference_basis), ...scalarList(block.inferred_from),
    ],
    withheld,
  };
}

// ── 没有裁决时的降级路径 ──────────────────────────────────────────────────────

type Degraded = {
  origin: LanguageOrigin;
  codes: string[];
  inferredCodes: string[];
  basisFields: string[];
  withheld: boolean;
};

/**
 * 降级路径的**全部**出口只有三个:「推断」「来源不明」「未知」。
 * 没有第四个 —— 尤其**没有通向「自报」的那一个**。
 *
 * 「推断」只由一条结构性口径给出:值取自一个**名字里就写着推断**的键
 * (`language_inferred` / `inferred_language` / 嵌套块的 `inferred_value(s)`),
 * 与服务端把 `vkpi_kol_pool.language_inferred` 整列判成推断档同源。这是读键名,
 * 不是拿词表去猜来源串,更不是在浏览器里判语言。
 *
 * **但光看键名不够**:同一行还写着这一票的把握度(`language_inferred_confidence`)。
 * 没过门槛(含读不出档位)的那一票按「试过、把握不够」处理 —— 计进未知,
 * 不算推断档,值也不上墙。这与服务端在同一行上的判法逐字一致。
 *
 * provider 载荷(`raw_platform_data` 及其嵌套 `raw`)不吃这条口径:它里面的
 * `language_inferred` 是 provider 说的话,不是我们那一列 —— 至多「来源不明」。
 */
function degradedPick(local: readonly Row[], provider: readonly Row[]): Degraded {
  const inferredCodes: string[] = [];
  // 试过、但把握不够门槛的那一票:只登记在这里,既不当结论也不进推断组。
  const withheldCodes: string[] = [];
  const basisFields: string[] = [];
  // 先到先得,后来的不许翻案 —— 用数组装,免得闭包里的赋值把类型收窄成 null。
  const picked: Array<{ codes: string[]; origin: LanguageOrigin }> = [];

  const take = (codes: string[], origin: LanguageOrigin) => {
    if (!picked.length && codes.length) picked.push({ codes: uniq(codes), origin });
  };

  /**
   * 推断值**必须先过把握度门槛**才算得上「推断」。
   *
   * 这条路上原来只看键名(名字里写着 inferred 就判推断),**同一行的把握度一个字都不读**
   * —— 于是后端在这行上判「未知(试过、没敢用)」的那一票,到了 KOL 池 / 详情抽屉
   * 就被升格成「推断 KO」。升格是这一格最贵的错:它把一句「我们没把握」讲成了结论。
   */
  const takeInferred = (codes: string[], confidence: unknown) => {
    if (!codes.length) return;
    if (!meetsConfidenceFloor(confidence)) {
      withheldCodes.push(...codes);
      return;
    }
    inferredCodes.push(...codes);
    take(codes, "inferred");
  };

  local.forEach((entry) => {
    // 把握度存在行上(迁移 305 的四列之一);证据块自带一份时以块里那份为准。
    const rowConfidence = CONFIDENCE_KEYS.map((key) => entry[key]).find((value) => value != null);
    verdictBlocks(entry).forEach((block) => {
      take(languageCodes(block.values, block.value), "projected");
      takeInferred(
        languageCodes(block.inferred_value, block.inferred_values),
        block.inference_confidence ?? block.confidence ?? rowConfidence,
      );
      basisFields.push(...scalarList(block.evidence_fields), ...scalarList(block.basis));
    });
    take(languageCodes(entry.language, entry.content_language), "projected");
    takeInferred(languageCodes(codeOf(entry.language_inferred), entry.inferred_language), rowConfidence);
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

  // provider 载荷排在最后,而且只有一个出口:「来源不明」。
  provider.forEach((entry) => {
    verdictBlocks(entry).forEach((block) => {
      take(languageCodes(block.values, block.value, block.inferred_value, block.inferred_values), "projected");
    });
    take(
      languageCodes(
        entry.language, entry.content_language,
        codeOf(entry.language_inferred), entry.inferred_language,
      ),
      "projected",
    );
  });

  const [result] = picked;
  const origin = result ? result.origin : "unknown";
  return {
    origin,
    codes: result ? result.codes : [],
    inferredCodes: uniq(inferredCodes),
    basisFields,
    // 「试着判断过、把握不够」这句话在这条路上也有出处:行上(或证据块里)确实躺着
    // 一票推断值,只是它的把握度没过门槛。什么都没落地时才说得出口 ——
    // 已经显示出别的值的那一格,不必再交代这一票。
    withheld: origin === "unknown" && withheldCodes.length > 0,
  };
}

// ── 文案 ──────────────────────────────────────────────────────────────────────

// 「什么都没有」那一档。**只陈述查得到的事实**:我们这里没有拿到值。
// 不许写「我们也没有足够的文字可以推断出来」—— 有没有试过推,这一档根本不知道。
const UNKNOWN_TITLE = "我们这里没有这个人的语言：平台资料里没有可用的值，也没有拿到推断出来的值。";
const UNKNOWN_NOTE = "我们这里没有语言信息";
// 第五种形态:服务端亲口说了它试过(旁挂了一票),但它自己觉得把握不够、没敢用。
// 这时候「试过」不是门面替系统吹的牛,是裁决里写着的事实,所以说得出口。
const WITHHELD_TITLE = "我们这里没有这个人自己填的语言：照他发的东西试着判断过，但把握不够，没有把它当成结论。";
const WITHHELD_NOTE = "试着判断过，但把握不够，没当结论";
// 第三档。**只说查得到的两件事**:资料里有这个值(所以值照显示);
// 看不出是不是他自己填的(所以不许说「他自己填的」)。
const PROJECTED_LABEL = "来源不明";
const PROJECTED_NOTE = "资料里有这个值，但看不出是不是他自己填的";
const PROJECTED_TITLE = "资料里有这个值，但我们看不出是不是他自己填的。";

// ── 入口 ──────────────────────────────────────────────────────────────────────

function normalizeInput(input: readonly unknown[] | LanguageRecordSet): { local: Row[]; provider: Row[] } {
  if (Array.isArray(input)) return { local: input.map(row), provider: [] };
  const set = input as LanguageRecordSet;
  return { local: (set.local || []).map(row), provider: (set.provider || []).map(row) };
}

/**
 * 从服务端给的若干层记录里读出「语言是什么 + 谁说的 + 凭什么」。
 *
 * **有裁决就照裁决渲染**:按记录顺序找到的第一张裁决说了算,不与别的层「合议」,
 * 也不拿别的层的原料去补全它 —— 裁决说未知就是未知,哪怕另一层还躺着一个值。
 * 调用方传进来的顺序就是优先级(例如 `[qualification, candidateFacets, root, source]`)。
 *
 * **没有裁决**才走降级路径,而降级路径没有通向「自报」的出口。
 */
export function resolveLanguageProvenance(
  input: readonly unknown[] | LanguageRecordSet,
): LanguageProvenance {
  const { local, provider } = normalizeInput(input);
  const verdictBlock = local.flatMap(verdictBlocks).find(isVerdict);
  const verdict = verdictBlock ? readVerdict(verdictBlock) : null;
  const resolved = verdict || degradedPick(local, provider);

  const { origin, codes, inferredCodes, basisFields, withheld } = resolved;
  // 「他自己填的是……」这半句是**替他转述一句话**,只有服务端指名道姓说了才许说。
  // 没有裁决 = 没有人说过这句话 = 一个字都不许出现。
  const selfReportedCodes = verdict && origin === "self_reported" ? verdict.selfReportedCodes : [];

  const codeLabel = codes.map(codeLabelOf).join("/");
  const nameLabel = nameList(codes);
  const basisLabel = origin === "inferred" ? basisLabelOf(basisFields) : "";
  // 他自己填的和我们推断的对不上时如实说出来,不许悄悄抹平成一个值。
  const divergenceLabel = selfReportedCodes.length && inferredCodes.length
    && !sameCodes(selfReportedCodes, inferredCodes)
    ? `他自己填的是${nameList(selfReportedCodes)}，照他发的东西推断出来的是${nameList(inferredCodes)}。`
    : "";
  const shared = {
    codes, selfReportedCodes, inferredCodes, divergenceLabel,
    hasServerVerdict: verdict !== null,
    inferenceWithheld: withheld,
  };

  if (origin === "unknown") {
    return {
      ...shared,
      // 说「未知」就得从头到尾都未知:这一档不许漏出任何一个具体语言值 ——
      // 包括服务端**自己没敢用**的那一票(不许升格),以及旁挂的两组码。
      codes: [],
      selfReportedCodes: [],
      inferredCodes: [],
      divergenceLabel: "",
      origin,
      displayLabel: "未知",
      nameLabel: "",
      originLabel: "未知",
      basisLabel: "",
      noteLabel: withheld ? WITHHELD_NOTE : UNKNOWN_NOTE,
      title: withheld ? WITHHELD_TITLE : UNKNOWN_TITLE,
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
  if (origin === "projected") {
    return {
      ...shared,
      origin,
      // 值照显示 —— 资料里确实有它,藏起来反而是另一种不诚实。
      // 但一个字都不说「他自己填的」,也不说「我们推断的」。
      displayLabel: codeLabel,
      nameLabel,
      originLabel: PROJECTED_LABEL,
      basisLabel: "",
      noteLabel: PROJECTED_NOTE,
      title: `${nameLabel} · ${PROJECTED_TITLE}${divergenceLabel}`,
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

/**
 * KOL 抽屉 / 池行的取数口径:一条本地行 + 若干份抓取器载荷。
 *
 * 单独成函数是为了让「哪一路是我们的列、哪一路是 provider 说的话」写死在一个地方 ——
 * 调用方再也不能顺手把载荷塞进本地那一路(那正是第三轮复核抓到的那个错)。
 */
export function kolLanguageProvenance(
  localRecord: unknown,
  providerRecords: readonly unknown[],
): LanguageProvenance {
  return resolveLanguageProvenance({ local: [localRecord], provider: providerRecords });
}

export type LanguageOriginCounts = {
  selfReported: number;
  inferred: number;
  projected: number;
  unknown: number;
};

/**
 * 统计条按**服务端裁的档**数人,门面不另立名目。
 *
 * 「服务端试过但没敢用」那一票 `origin` 就是 unknown,所以它天然落在「未知」那一格 ——
 * **不许计进「推断」**,那是升格。
 */
export function languageOriginCounts(values: readonly LanguageProvenance[]): LanguageOriginCounts {
  return {
    selfReported: values.filter((value) => value.origin === "self_reported").length,
    inferred: values.filter((value) => value.origin === "inferred").length,
    projected: values.filter((value) => value.origin === "projected").length,
    unknown: values.filter((value) => value.origin === "unknown").length,
  };
}

/**
 * 统计条:只有真出现过「自报」以外的档才值得占一格,全是自报时不喂噪音。
 *
 * 出现了就必须报出来 —— 少列一档,这一栏的几个数字加起来就对不上人数,
 * 那几个没被数进去的人会被顺手当成「自报」。
 */
export function languageOriginSummaryLabel(counts: LanguageOriginCounts): string {
  if (!counts.inferred && !counts.projected && !counts.unknown) return "";
  const parts = [`自报 ${counts.selfReported}`];
  if (counts.inferred) parts.push(`推断 ${counts.inferred}`);
  if (counts.projected) parts.push(`${PROJECTED_LABEL} ${counts.projected}`);
  if (counts.unknown) parts.push(`未知 ${counts.unknown}`);
  return `语言 · ${parts.join(" · ")}`;
}
