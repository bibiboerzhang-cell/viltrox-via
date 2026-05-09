import React from 'react';

export function PageShell({ title, description, children, side }: { title: string; description: string; children: React.ReactNode; side?: React.ReactNode }) {
  return (
    <>
      <section className="vkpi-main-column vkpi-workspace">
        <div className="vkpi-page-heading">
          <div>
            <span>VILTROX MARKETING</span>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
        </div>
        {children}
      </section>
      {side}
    </>
  );
}
