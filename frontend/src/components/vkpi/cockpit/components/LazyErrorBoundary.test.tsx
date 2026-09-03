import React from "react";
import fs from "node:fs";
import path from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LazyErrorBoundary } from "./LazyErrorBoundary";
import { BoardLoadingFallback, BoardPlaceholderCard } from "./BoardPlaceholders";

function BrokenModal(): React.ReactElement {
  throw new Error("modal render failed");
}

// 写死调色板(white/slate/purple…)在 6 套主题里至少有一边露馅;兜底/占位只允许 --ds-* token 类。
const HARDCODED_PALETTE = /\b(?:text|bg|border)-(?:white|black|slate|gray|amber|emerald|purple|blue)(?:-\d+)?(?:\/[\d.[\]]+)?\b/;
const THEME_COMBOS = [
  ["glass", "light"], ["glass", "dark"],
  ["instrument", "light"], ["instrument", "dark"],
  ["commandos", "light"], ["commandos", "dark"],
] as const;

function expectTokenOnly(root: ParentNode) {
  for (const node of Array.from(root.querySelectorAll<HTMLElement>("[class]"))) {
    expect(node.getAttribute("class") || "", node.outerHTML.slice(0, 140)).not.toMatch(HARDCODED_PALETTE);
  }
}

// tokens.css 六套 --ds-text / --ds-text-2 相对 --ds-bg-2 的对比度(兜底卡正文 = text-ink / text-ink-2)。
const tokensSource = fs.readFileSync(path.resolve(__dirname, "../../../../styles/tokens.css"), "utf8");
function luminance(hex: string): number {
  const raw = /^#([0-9a-f]{6})$/i.exec(hex.trim())?.[1];
  if (!raw) throw new Error(`expected hex colour, received ${hex}`);
  const channels = [0, 2, 4].map((offset) => Number.parseInt(raw.slice(offset, offset + 2), 16) / 255)
    .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
function tokenBlock(style: string, theme: string): string {
  const selector = `[data-style="${style}"][data-theme="${theme}"]`.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${selector}\\s*\\{([\\s\\S]*?)\\n\\}`).exec(tokensSource);
  if (!match) throw new Error(`missing token block for ${style}/${theme}`);
  return match[1];
}
function tokenValue(block: string, name: string): string {
  const match = new RegExp(`${name}:\\s*([^;]+);`).exec(block);
  if (!match) throw new Error(`missing ${name}`);
  return match[1].trim();
}

afterEach(() => {
  vi.restoreAllMocks();
  delete document.documentElement.dataset.style;
  delete document.documentElement.dataset.theme;
});

describe("LazyErrorBoundary", () => {
  it("isolates a lazy modal failure in a dismissible overlay", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onDismiss = vi.fn();
    render(
      <LazyErrorBoundary name="活动详情" variant="overlay" onDismiss={onDismiss}>
        <BrokenModal />
      </LazyErrorBoundary>,
    );

    expect(screen.getByRole("dialog", { name: "活动详情 加载失败" })).toHaveTextContent("活动详情 暂时出错");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("renders the error card with theme tokens only, in every style × theme combination (U-B1)", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    for (const [style, theme] of THEME_COMBOS) {
      document.documentElement.dataset.style = style;
      document.documentElement.dataset.theme = theme;
      const { container } = render(
        <LazyErrorBoundary name="KolPool">
          <BrokenModal />
        </LazyErrorBoundary>,
      );
      const alert = screen.getByRole("alert", { name: "KolPool 加载失败" });
      expect(alert).toHaveTextContent("KolPool 暂时出错");
      expect(alert).toHaveTextContent("modal render failed");
      expectTokenOnly(container);
      expect(container.querySelector(".text-ink")).not.toBeNull();
      cleanup();
    }
  });

  it("keeps the overlay scrim on a token instead of a fixed black", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <LazyErrorBoundary name="X" variant="overlay">
        <BrokenModal />
      </LazyErrorBoundary>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("style") || "").toContain("var(--ds-scrim)");
    expect(dialog.className).not.toMatch(HARDCODED_PALETTE);
  });
});

describe("board placeholders (U-B1)", () => {
  it("loading fallback and not-wired card use token classes only", () => {
    const onBack = vi.fn();
    const { container } = render(
      <>
        <BoardLoadingFallback label="KOL Pool" />
        <BoardPlaceholderCard label="Skill Studio" icon={null} onBack={onBack} />
      </>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("KOL Pool 加载中...");
    expect(screen.getByText("Skill Studio")).toHaveClass("text-ink");
    fireEvent.click(screen.getByRole("button", { name: "← 返回 Dashboard" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expectTokenOnly(container);
  });

  it("text tokens stay readable on every theme surface (WCAG AA)", () => {
    for (const [style, theme] of THEME_COMBOS) {
      const block = tokenBlock(style, theme);
      const bg = tokenValue(block, "--ds-bg-2");
      expect(contrast(tokenValue(block, "--ds-text"), bg), `${style}/${theme} --ds-text`).toBeGreaterThanOrEqual(7);
      expect(contrast(tokenValue(block, "--ds-text-2"), bg), `${style}/${theme} --ds-text-2`).toBeGreaterThanOrEqual(4.5);
    }
  });
});
