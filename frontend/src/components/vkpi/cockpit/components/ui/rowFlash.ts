// 车道B(2026-07-07):行级「一次性变更高亮」基元(soft pulse,400ms 一次)。
// ----------------------------------------------------------------------------
// 审美纪律(与 motion.css 头注释同源):只在「值变化」时高亮一次,绝无循环;
// animationend 自动摘 class;prefers-reduced-motion 直接不挂 class
// (motion.css 的 reduce 块另有 animation:none 兜底,双保险)。
// 用法:const ref = useRowFlash<HTMLDivElement>(watchedValue) → 挂到行根元素;
//   watchedValue 变化(首帧不算)→ 挂 .vk-row-flash → 动画结束自摘。
//   { flashOnMount: true } 供「新条目入列」场景(如 ActivityFeed 增量条目):
//   挂载即闪一次,由调用方保证首屏整批不传 true(避免齐闪)。
import { useEffect, useRef } from "react";
import { usePrefersReducedMotion } from "../AnimatedNumber";
import "./motion.css";

export const ROW_FLASH_CLASS = "vk-row-flash";

// 「尚未观察过任何值」哨兵:与一切业务值(含 undefined/null)可区分。
const UNSET = Symbol("row-flash-unset");

export function useRowFlash<T extends HTMLElement>(
  value: unknown,
  { flashOnMount = false }: { flashOnMount?: boolean } = {},
): React.RefObject<T> {
  // useRef<T>(null) → RefObject<T>(React 18 类型口径),可直挂 DOM/m.* 的 ref。
  const ref = useRef<T>(null);
  const reduced = usePrefersReducedMotion();
  const prevRef = useRef<unknown>(UNSET);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = value;
    const first = prev === UNSET;
    // 首帧只有 flashOnMount 才闪;后续帧只有值真的变了才闪(Object.is 口径)。
    if (first ? !flashOnMount : Object.is(prev, value)) return undefined;
    const el = ref.current;
    if (!el || reduced) return undefined;
    // 重入(动画未结束又来一次变更):先摘旧 class + 强制 reflow,让动画可重触发。
    el.classList.remove(ROW_FLASH_CLASS);
    void el.offsetWidth;
    el.classList.add(ROW_FLASH_CLASS);
    const onEnd = (event: AnimationEvent) => {
      if (event.target === el) el.classList.remove(ROW_FLASH_CLASS);
    };
    el.addEventListener("animationend", onEnd);
    return () => {
      el.removeEventListener("animationend", onEnd);
      el.classList.remove(ROW_FLASH_CLASS);
    };
  }, [value, reduced, flashOnMount]);

  return ref;
}

export default useRowFlash;
