interface BigNumberCardProps {
  title: string;
  value: string;
  delta?: string;
  tone?: 'positive' | 'negative' | 'neutral';
}

export function BigNumberCard({ title, value, delta, tone = 'neutral' }: BigNumberCardProps) {
  return (
    <div className="da-big-card">
      <div className="da-big-card__title">{title}</div>
      <div className="da-big-card__value">{value}</div>
      {delta ? (
        <div className={`da-big-card__delta da-big-card__delta--${tone}`}>
          {tone === 'positive' ? '▲' : tone === 'negative' ? '▼' : '·'} {delta}
        </div>
      ) : null}
    </div>
  );
}
