// browserAssist/executors.ts — 浏览器内本地协助的纯计算执行器。
//
// 员工开着页面时,浏览器后台领「安全轻活」在本机算,分担服务器算力(无感、零安装)。
// 只放纯计算任务:comment_clean(文字去重/规范化/语言判断)。视频类(yt-dlp/ffmpeg)、
// 走 LLM 的(sentiment)一律留服务器,浏览器干不了也不该干。
//
// 输出严格对齐后端 local_workers/validation.py 契约(result.comments 为对象数组,
// 每项带非空 text + 语言字段 lang),否则服务端深校验 validated=0。

/** 极轻量语言启发(仅本地预筛,非权威):CJK 段判 zh/ja/ko,否则 en/und。 */
export function guessLang(text: string): string {
  if (/[가-힣]/.test(text)) return "ko"; // 韩文谚文
  if (/[぀-ヿ]/.test(text)) return "ja"; // 日文假名
  if (/[一-鿿]/.test(text)) return "zh"; // 中日韩汉字
  if (/[a-zA-Z]/.test(text)) return "en";
  return "und";
}

export interface CleanComment {
  text: string;
  lang: string;
}

export interface CommentCleanResult {
  task: "comment_clean";
  url: string;
  input_count: number;
  cleaned_count: number;
  duplicates_removed: number;
  comments: CleanComment[];
}

/**
 * 评论清洗:去空白 / 大小写不敏感去重 / 语言预判。纯函数、无网络、无外部依赖。
 * 与 scripts/vkpi_local_runner.py 的 exec_comment_clean 同口径(便于双端一致)。
 */
export function execCommentClean(payload: Record<string, unknown>): CommentCleanResult {
  const rawList = Array.isArray(payload?.comments)
    ? (payload.comments as unknown[])
    : Array.isArray(payload?.items)
      ? (payload.items as unknown[])
      : [];
  const seen = new Set<string>();
  const cleaned: CleanComment[] = [];
  for (const raw of rawList) {
    const source = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : null;
    const rawText = source ? source.text : raw;
    const text = String(rawText ?? "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const key = text.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    const providedLang = source ? source.lang ?? source.language : null;
    cleaned.push({ text, lang: providedLang ? String(providedLang) : guessLang(text) });
  }
  return {
    task: "comment_clean",
    url: String(payload?.url ?? ""),
    input_count: rawList.length,
    cleaned_count: cleaned.length,
    duplicates_removed: rawList.length - cleaned.length,
    comments: cleaned.slice(0, 500),
  };
}

/** 浏览器可跑的安全任务类型 → 执行器。视频/LLM 类不在此表,浏览器永不领。 */
export const BROWSER_EXECUTORS: Record<
  string,
  (payload: Record<string, unknown>) => { result: unknown; files_meta: unknown[] }
> = {
  comment_clean: (payload) => ({ result: execCommentClean(payload), files_meta: [] }),
};

/** 浏览器本轮能领的任务类型(供 lease 请求过滤,只领算得动的)。 */
export const BROWSER_TASK_TYPES: string[] = Object.keys(BROWSER_EXECUTORS);
