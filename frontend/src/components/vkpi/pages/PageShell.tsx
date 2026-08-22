import React from 'react';
import { useT } from '../cockpit/lib/i18n';

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
  const { t } = useT();
  return (
    <>
      <section className="vkpi-main-column vkpi-workspace">
        {hideHeading ? null : (
          <div className={`vkpi-page-heading${headingExtra ? ' vkpi-page-heading--split' : ''}`}>
            <div>
              {eyebrow ? <span>{t(eyebrow)}</span> : null}
              <h1>{t(title)}</h1>
              {description ? <p>{t(description)}</p> : null}
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
