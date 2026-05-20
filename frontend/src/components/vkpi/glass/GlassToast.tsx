import type { ReactNode } from 'react';

interface GlassToastProps {
  children?: ReactNode;
  id?: string;
  show?: boolean;
}

export function GlassToast({ children = '已触发', id = 'toast', show = false }: GlassToastProps) {
  return (
    <div className={`toast${show ? ' show' : ''}`} id={id}>{children}</div>
  );
}
