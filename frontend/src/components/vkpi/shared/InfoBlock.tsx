export function InfoBlock({ label, value, tone }: { label: string; value: string; tone?: 'good' | 'warn' }) {
  return <div className={`vkpi-info-block ${tone ? `is-${tone}` : ''}`}><span>{label}</span><strong>{value}</strong></div>;
}
