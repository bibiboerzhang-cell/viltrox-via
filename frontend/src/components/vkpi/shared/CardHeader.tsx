export function CardHeader({ title, action }: { title: string; action?: string }) {
  return (
    <div className="vkpi-card__header">
      <h2>{title}</h2>
      {action ? <button className="vkpi-mini-button" type="button">{action}⌄</button> : null}
    </div>
  );
}
