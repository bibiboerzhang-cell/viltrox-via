import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("development runtime bootstrap", () => {
  it("does not replace the React DevTools global hook and disable Fast Refresh", () => {
    const html = readFileSync("index.html", "utf8");
    expect(html).not.toContain("__REACT_DEVTOOLS_GLOBAL_HOOK__");
  });
});
