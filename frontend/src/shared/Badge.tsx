import type { HTMLAttributes } from "react";

import type { Tone } from "./Button";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
  variant?: "soft" | "outline" | "solid";
  pill?: boolean;
}

/** 语义色标签,tone→--ds-* token。 */
export function Badge({ tone = "neutral", variant = "soft", pill, className = "", children, ...rest }: BadgeProps) {
  return (
    <span
      className={`ds-badge ${className}`.trim()}
      data-tone={tone}
      data-variant={variant}
      data-shape={pill ? "pill" : undefined}
      {...rest}
    >
      {children}
    </span>
  );
}
