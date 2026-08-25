import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const source = fs.readFileSync(path.resolve(__dirname, "tokens.css"), "utf8");

function hexRgb(value: string): [number, number, number] {
  const match = /^#([0-9a-f]{6})$/i.exec(value.trim());
  if (!match) throw new Error(`expected six-digit hex colour, received ${value}`);
  const raw = match[1];
  return [0, 2, 4].map((offset) => Number.parseInt(raw.slice(offset, offset + 2), 16)) as [number, number, number];
}

function luminance(value: string): number {
  const channels = hexRgb(value).map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

function variable(block: string, name: string): string {
  const match = new RegExp(`${name}:\\s*([^;]+);`).exec(block);
  if (!match) throw new Error(`missing ${name}`);
  return match[1].trim();
}

describe("employee-facing design tokens", () => {
  it("keeps status/meta text at WCAG AA contrast in every theme", () => {
    const selectors = [
      '[data-style="glass"][data-theme="light"]',
      '[data-style="glass"][data-theme="dark"]',
      '[data-style="instrument"][data-theme="light"]',
      '[data-style="instrument"][data-theme="dark"]',
      '[data-style="commandos"][data-theme="light"]',
      '[data-style="commandos"][data-theme="dark"]',
    ];
    for (const selector of selectors) {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const match = new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\n\\}`).exec(source);
      expect(match, selector).not.toBeNull();
      const block = match?.[1] || "";
      expect(contrast(variable(block, "--ds-text-meta"), variable(block, "--ds-bg-2")), selector).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("defines readable status-copy and compact-control minimums", () => {
    expect(source).toContain("--ds-fs-body-sm: 12px;");
    expect(source).toContain("--ds-fs-meta: 11.5px;");
    expect(source).toContain("--ds-control-sm: 36px;");
  });
});
