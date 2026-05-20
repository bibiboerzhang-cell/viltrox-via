import type { CSSProperties, ReactNode } from 'react';

export type GlassCssVars = CSSProperties & Record<`--${string}`, string | number>;

export type GlassButtonVariant = 'default' | 'primary' | 'sync';

export interface GlassNavItem {
  key: string;
  label: ReactNode;
  badge?: ReactNode;
  icon: ReactNode;
}

export interface GlassMission {
  value: ReactNode;
  suffix: ReactNode;
  label: ReactNode;
}

export interface GlassTopAction {
  label: ReactNode;
  variant?: GlassButtonVariant;
  onClick?: () => void;
}

export function glassVarStyle(vars: Record<`--${string}`, string | number>): GlassCssVars {
  return vars as GlassCssVars;
}
