import type { ButtonHTMLAttributes, ReactNode } from 'react';
import type { GlassButtonVariant } from './tokens';

interface GlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: GlassButtonVariant;
}

function variantClass(variant: GlassButtonVariant) {
  if (variant === 'primary') return ' primary';
  if (variant === 'sync') return ' sync';
  return '';
}

export function GlassButton({ children, variant = 'default', className = '', type = 'button', ...props }: GlassButtonProps) {
  return (
    <button className={`glass-btn${variantClass(variant)}${className ? ` ${className}` : ''}`} type={type} {...props}>
      {children}
    </button>
  );
}
