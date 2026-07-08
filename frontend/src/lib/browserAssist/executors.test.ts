import { describe, expect, it } from "vitest";

import { execCommentClean, guessLang, BROWSER_TASK_TYPES } from "./executors";

describe("browserAssist executors", () => {
  it("guessLang 识别中英日韩", () => {
    expect(guessLang("great lens")).toBe("en");
    expect(guessLang("很锐利")).toBe("zh");
    expect(guessLang("すごい")).toBe("ja");
    expect(guessLang("좋아요")).toBe("ko");
    expect(guessLang("123 !!!")).toBe("und");
  });

  it("comment_clean 去重/去空白/带语言,对齐服务端契约", () => {
    const r = execCommentClean({
      url: "https://youtu.be/x",
      comments: [
        { text: "  great lens  " },
        { text: "great lens" }, // 重复(大小写/空白归一后)
        { text: "GREAT LENS" }, // 重复
        { text: "" }, // 空,丢弃
        { text: "很锐利的镜头" },
      ],
    });
    expect(r.task).toBe("comment_clean");
    expect(r.url).toBe("https://youtu.be/x");
    expect(r.input_count).toBe(5);
    expect(r.cleaned_count).toBe(2);
    // 5 条:2 保留 + 2 真重复 + 1 空 → 重复只算 2,空单独算 1(不再混为一谈)。
    expect(r.duplicates_removed).toBe(2);
    expect(r.empty_removed).toBe(1);
    // 契约:comments 为对象数组,每项带非空 text + lang。
    expect(r.comments).toEqual([
      { text: "great lens", lang: "en" },
      { text: "很锐利的镜头", lang: "zh" },
    ]);
  });

  it("透传已给的 lang,不覆盖", () => {
    const r = execCommentClean({ comments: [{ text: "hola", lang: "es" }] });
    expect(r.comments[0]).toEqual({ text: "hola", lang: "es" });
  });

  it("payload 无 comments 时安全返回空", () => {
    const r = execCommentClean({ url: "x" });
    expect(r.cleaned_count).toBe(0);
    expect(r.comments).toEqual([]);
  });

  it("BROWSER_TASK_TYPES 只含浏览器能跑的(无视频/LLM 类)", () => {
    expect(BROWSER_TASK_TYPES).toContain("comment_clean");
    expect(BROWSER_TASK_TYPES).not.toContain("download_frames");
    expect(BROWSER_TASK_TYPES).not.toContain("metadata_extract");
  });
});
