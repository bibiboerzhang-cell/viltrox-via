// U2 动效基元桶文件:供全库(W3-W6 波)一行引入。
// 纪律:纯展示零请求;动画克制(入场/变化各一次 + 呼吸/加载两类克制循环);
// prefers-reduced-motion 全部降级为静态(motion.css)。
export { Sparkline } from "./Sparkline";
export type { SparklineProps } from "./Sparkline";
export { FreshnessDot, computeFreshness } from "./FreshnessDot";
export type { FreshnessDotProps, FreshnessState } from "./FreshnessDot";
export { DeltaBadge } from "./DeltaBadge";
export type { DeltaBadgeProps } from "./DeltaBadge";
export { SkeletonBlock } from "./SkeletonBlock";
export type { SkeletonBlockProps } from "./SkeletonBlock";
