// U4 会呼吸的指挥室 · AnimatedNumber:大数字 count-up。
// ----------------------------------------------------------------------------
// 行为纪律(审美宪法):只在「入场」与「数据变化」时各补间一次(默认 300ms),
// 绝无常驻循环;prefers-reduced-motion 用户直显终值(直接读同名 CSS media query,
// 见 usePrefersReducedMotion,不依赖 framer-motion 的 hook —— 既贴规格字面,
// 也让本组件在「只 mock 了 motion/AnimatePresence」的既有测试环境下不崩)。
// 零新依赖:补间复用项目已有 framer-motion 的 animate(KOLDetailDrawer 同库),
// 且带函数存在性守卫:mock 环境缺 animate 时降级为直显。
// 数据契约:只做显示层补间,value 原样来自后端聚合,不改口径不编数。
// (前身为 vkpi_v6.15.7_integrated.html 的 verbatim 拷贝,全仓无引用,按 U4 规格重写。)

import React, { useEffect, useRef, useState } from "react";
import { animate } from "framer-motion";

const e = React.createElement;

const REDUCED_MQ = "(prefers-reduced-motion: reduce)";

// 读 prefers-reduced-motion(CSS media query),SSR / 无 matchMedia 环境安全回退 false。
// 导出给同波三卡复用,保证「降级为静态」口径全站一致。
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    try {
      return Boolean(window.matchMedia(REDUCED_MQ).matches);
    } catch {
      return false;
    }
  });
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return undefined;
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia(REDUCED_MQ);
    } catch {
      return undefined;
    }
    const onChange = () => setReduced(Boolean(mq.matches));
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    }
    return undefined;
  }, []);
  return reduced;
}

// 默认格式:整数 + 千分位(大数字仪表最常见口径)。百分比等场景由调用方传 format。
const defaultFormat = (v: number): React.ReactNode => Math.round(v).toLocaleString();

function toNumber(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  if (typeof value === "string") {
    const n = parseFloat(value.replace(/[^0-9.eE+-]/g, ""));
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

export function AnimatedNumber({
  value,
  format = defaultFormat,
  durationMs = 300,
  delayMs = 0,
}: {
  value: number | string | null | undefined;
  format?: (v: number) => React.ReactNode;
  durationMs?: number;
  delayMs?: number;
}) {
  const target = toNumber(value);
  const reduced = usePrefersReducedMotion();
  // 补间能力守卫:测试里 framer-motion 可能被 mock 成只有 motion/AnimatePresence。
  const canTween = typeof animate === "function";
  const still = reduced || !canTween;
  // 入场从 0 数到目标;后续数据变化从「当前显示值」补间到新值(各只动一次)。
  const [display, setDisplay] = useState<number>(still ? target : 0);
  const currentRef = useRef<number>(still ? target : 0);

  useEffect(() => {
    if (still) {
      currentRef.current = target;
      setDisplay(target);
      return undefined;
    }
    if (currentRef.current === target) return undefined;
    const controls = animate(currentRef.current, target, {
      duration: durationMs / 1000,
      delay: delayMs / 1000,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        currentRef.current = v;
        setDisplay(v);
      },
    });
    return () => controls.stop();
  }, [target, still, durationMs, delayMs]);

  return e("span", { "data-animated-number": "" }, format(display));
}

export default AnimatedNumber;
