import type { ReactNode } from 'react';

interface GlassFABProps {
  children?: ReactNode;
  onClick?: () => void;
}

export function GlassFAB({ children = '✦', onClick }: GlassFABProps) {
  return (
    <div className="fab" onClick={onClick}>{children}</div>
  );
}
