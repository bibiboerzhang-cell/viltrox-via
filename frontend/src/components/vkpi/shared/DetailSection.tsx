import React from 'react';

export function DetailSection({ title, action, children }: { title: string; action?: string; children: React.ReactNode }) {
  return (
    <section className="vkpi-detail-section">
      <header>
        <h4>{title}</h4>
        {action ? <button className="vkpi-link-button" type="button">{action}</button> : null}
      </header>
      {children}
    </section>
  );
}
