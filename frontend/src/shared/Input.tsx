import { forwardRef, type InputHTMLAttributes } from "react";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

/** 只吃 --ds-* token 的输入框。 */
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, className = "", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={`ds-input ${className}`.trim()}
      data-invalid={invalid || undefined}
      {...rest}
    />
  );
});
