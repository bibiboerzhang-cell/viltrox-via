export function CardHeader({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  return (
    <div className="vkpi-card__header">
      <h2>{title}</h2>
      {action && onAction ? <button className="vkpi-mini-button" type="button" onClick={onAction}>{action}⌄</button> : null}
    </div>
  );
}
