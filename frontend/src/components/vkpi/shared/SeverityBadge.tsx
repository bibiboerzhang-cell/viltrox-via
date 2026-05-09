export function SeverityBadge({ severity }: { severity: string }) {
  const normalized = String(severity || 'info').toLowerCase();
  const label = normalized === 'high' ? '高' : normalized === 'medium' ? '中' : normalized === 'low' ? '低' : normalized === 'critical' ? '严重' : '提示';
  return <span className={`vkpi-stage-badge is-${normalized === 'high' || normalized === 'critical' ? 'lost' : normalized === 'medium' ? 'stalled' : 'closed'}`}>{label}</span>;
}
