/**
 * PageHeader — tab page top (title + subtitle + actions)
 */
import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="ax-page-header">
      <div>
        <h1 className="ax-page-title">{title}</h1>
        {subtitle ? <p className="ax-page-subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="ax-page-header__actions">{actions}</div> : null}
    </div>
  );
}
