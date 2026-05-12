import React from 'react';

export function DetailSection({
  title,
  action,
  onAction,
  children,
}: {
  title: string;
  action?: string;
  onAction?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="vkpi-detail-section">
      <header>
        <h4>{title}</h4>
        {action && onAction ? <button className="vkpi-link-button" type="button" onClick={onAction}>{action}</button> : null}
      </header>
      {children}
    </section>
  );
}
