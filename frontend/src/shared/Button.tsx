import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

export type Tone = "neutral" | "brand" | "success" | "warning" | "danger" | "info";
export type ButtonVariant = "solid" | "soft" | "outline" | "ghost" | "link";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  tone?: Tone;
  size?: "xs" | "sm" | "md";
  iconOnly?: boolean;
  leadingIcon?: ReactNode;
  loading?: boolean;
  fullWidth?: boolean;
}

/** 只吃 --ds-* token 的按钮。variant×tone×size → data-* 属性,零硬编码色。 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "soft",
    tone = "neutral",
    size = "sm",
    iconOnly,
    leadingIcon,
    loading,
    fullWidth,
    disabled,
    children,
    className = "",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      className={`ds-btn ${className}`.trim()}
      data-variant={variant}
      data-tone={tone}
      data-size={size}
      data-icon={iconOnly || undefined}
      data-full={fullWidth || undefined}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <Loader2 size={14} className="ds-spin" /> : leadingIcon}
      {!iconOnly && children}
    </button>
  );
});
