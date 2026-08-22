// U8 守门:legacy(非 cockpit)页的内联 fontSize 不得再写 ≤13px 的裸数字——
// 正文/次级/标签字号必须走 type-scale token(src/styles/type-scale.css 的 --ds-fs-*),
// 否则员工反馈 #1「功能模块字体太小」的全站旋钮对这些页面失效。
// 标题层(≥14px)不在 token 范围,允许保留数字。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TYPE_SCALE = path.resolve(HERE, "../../../styles/type-scale.css");
const BARE_SMALL_FONT_SIZE = /fontSize:\s*(?:'|")?(7|7\.5|8|8\.5|9|9\.5|10|10\.5|11|11\.5|12|12\.5|13|13\.5)(?:px)?(?:'|")?(?=\s*[,}\n])/g;
const TOKEN_FONT_SIZE = /var\(--ds-fs-([0-9-]+)\)/g;

function sourceFiles(root: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const filePath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      out.push(...sourceFiles(filePath));
      continue;
    }
    if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) continue;
    out.push(filePath);
  }
  return out.sort();
}

describe("legacy pages inline font sizes use the type-scale tokens", () => {
  const files = sourceFiles(HERE);
  const definedTokens = new Set(
    Array.from(fs.readFileSync(TYPE_SCALE, "utf8").matchAll(/--ds-fs-([0-9-]+):/g)).map((match) => match[1]),
  );

  it("has no bare numeric fontSize at or below 13.5px", () => {
    const offenders: string[] = [];
    for (const filePath of files) {
      const source = fs.readFileSync(filePath, "utf8");
      for (const match of source.matchAll(BARE_SMALL_FONT_SIZE)) {
        const line = source.slice(0, match.index).split("\n").length;
        offenders.push(`${path.relative(HERE, filePath)}:${line} fontSize ${match[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("only references --ds-fs-* tokens that type-scale.css defines", () => {
    const unknown: string[] = [];
    for (const filePath of files) {
      const source = fs.readFileSync(filePath, "utf8");
      for (const match of source.matchAll(TOKEN_FONT_SIZE)) {
        if (!definedTokens.has(match[1])) unknown.push(`${path.relative(HERE, filePath)} --ds-fs-${match[1]}`);
      }
    }
    expect(unknown).toEqual([]);
  });
});
