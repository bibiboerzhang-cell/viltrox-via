import React from 'react';

interface PageShellProps {
  title: string;
  description?: string;
  children: React.ReactNode;
  side?: React.ReactNode;
  eyebrow?: string | null;
  headingExtra?: React.ReactNode;
  hideHeading?: boolean;
}

export function PageShell({ title, description, children, side, eyebrow = 'VILTROX MARKETING', headingExtra, hideHeading = false }: PageShellProps) {
  return (
    <>
      <section className="vkpi-main-column vkpi-workspace">
        {hideHeading ? null : (
          <div className={`vkpi-page-heading${headingExtra ? ' vkpi-page-heading--split' : ''}`}>
            <div>
              {eyebrow ? <span>{eyebrow}</span> : null}
              <h1>{title}</h1>
              {description ? <p>{description}</p> : null}
            </div>
            {headingExtra ? <div className="vkpi-page-heading__extra">{headingExtra}</div> : null}
          </div>
        )}
        {children}
      </section>
      {side}
    </>
  );
}
