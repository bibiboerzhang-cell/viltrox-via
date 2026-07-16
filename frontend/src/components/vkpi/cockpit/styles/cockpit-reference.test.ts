import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ENTRY_PATH = path.join(HERE, "cockpit-reference.css");
const SOURCE_FILES = [
  "cockpit-reference.shell.css",
  "cockpit-reference.chrome.css",
  "cockpit-reference.routes.css",
  "cockpit-reference.modules.css",
] as const;

function lineCount(source: string): number {
  return source.length === 0 ? 0 : source.replace(/\r?\n$/, "").split(/\r?\n/).length;
}

describe("cockpit reference stylesheet split", () => {
  it("keeps the cascade imports in their original order", () => {
    const entry = fs.readFileSync(ENTRY_PATH, "utf8");

    expect(entry.trim().split(/\r?\n/)).toEqual(
      SOURCE_FILES.map((file) => `@import "./${file}";`),
    );
  });

  it.each(SOURCE_FILES)("keeps %s as a leaf stylesheet within the line limit", (file) => {
    const source = fs.readFileSync(path.join(HERE, file), "utf8");

    expect(source).not.toMatch(/^\s*@import\b/m);
    expect(lineCount(source)).toBeLessThanOrEqual(1000);
  });
});
