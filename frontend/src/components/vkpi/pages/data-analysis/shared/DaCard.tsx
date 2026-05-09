import type { ReactNode } from 'react';

interface DaCardProps {
  title: string;
  eyebrow?: string;
  side?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}

export function DaCard({ title, eyebrow, side, wide, children }: DaCardProps) {
  return (
    <section className={`da-card${wide ? ' da-card--wide' : ''}`}>
      <header className="da-card__header">
        <div className="da-card__title-block">
          {eyebrow ? <span className="da-card__eyebrow">{eyebrow}</span> : null}
          <h3 className="da-card__title">{title}</h3>
        </div>
        {side ? <div className="da-card__side">{side}</div> : null}
      </header>
      {children}
    </section>
  );
}
