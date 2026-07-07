import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// U4:AnimatedNumber count-up 冒烟。jsdom(vitest pretendToBeVisual)有 rAF,
// framer-motion animate 真跑;300ms 一次到位后应稳定停在终值(无循环)。
import { AnimatedNumber } from "./AnimatedNumber";

describe("AnimatedNumber count-up", () => {
  it("数值入场 count-up:最终显示格式化终值(默认整数千分位)", async () => {
    render(
      <span data-testid="n">
        <AnimatedNumber value={1234} format={(v) => String(Math.round(v))} />
      </span>,
    );
    // 入场从 0 起步补间,300ms 内到达终值并停住。
    await waitFor(
      () => expect(screen.getByTestId("n")).toHaveTextContent(/^1234$/),
      { timeout: 2000 },
    );
    // 动画属性断言:输出带 data-animated-number 标记(供上层/测试识别)。
    expect(
      screen.getByTestId("n").querySelector("[data-animated-number]"),
    ).not.toBeNull();
  });

  it("字符串数值可解析(剥非数字字符)", async () => {
    render(
      <span data-testid="s">
        <AnimatedNumber value={"45.6%"} format={(v) => v.toFixed(1)} />
      </span>,
    );
    await waitFor(
      () => expect(screen.getByTestId("s")).toHaveTextContent(/^45\.6$/),
      { timeout: 2000 },
    );
  });

  it("null / 非法值一律按 0 直显,不崩", async () => {
    render(
      <span data-testid="z">
        <AnimatedNumber value={null} format={(v) => String(Math.round(v))} />
      </span>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("z")).toHaveTextContent(/^0$/),
    );
  });

  it("数据变化时补间到新值(变化各一次)", async () => {
    const { rerender } = render(
      <span data-testid="c">
        <AnimatedNumber value={10} format={(v) => String(Math.round(v))} />
      </span>,
    );
    await waitFor(
      () => expect(screen.getByTestId("c")).toHaveTextContent(/^10$/),
      { timeout: 2000 },
    );
    rerender(
      <span data-testid="c">
        <AnimatedNumber value={99} format={(v) => String(Math.round(v))} />
      </span>,
    );
    await waitFor(
      () => expect(screen.getByTestId("c")).toHaveTextContent(/^99$/),
      { timeout: 2000 },
    );
  });
});
