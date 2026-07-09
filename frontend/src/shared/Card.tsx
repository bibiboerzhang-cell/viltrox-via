import { forwardRef, type HTMLAttributes } from "react";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  pad?: "sm" | "md" | "lg";
  blur?: boolean;
  interactive?: boolean;
}

/** 玻璃/实心卡片,阴影/圆角/背景全走 --ds-* token(风格自动切换)。 */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { pad = "md", blur, interactive, className = "", children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={`ds-card ${className}`.trim()}
      data-pad={pad}
      data-blur={blur || undefined}
      data-interactive={interactive || undefined}
      {...rest}
    >
      {children}
    </div>
  );
});
