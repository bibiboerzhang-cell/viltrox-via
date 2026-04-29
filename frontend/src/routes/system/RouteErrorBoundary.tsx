import { Link, isRouteErrorResponse, useRouteError } from "react-router-dom";
import { useTranslation } from "react-i18next";

function resolveErrorKey(error: unknown): "notFound" | "generic" {
  if (isRouteErrorResponse(error) && error.status === 404) {
    return "notFound";
  }
  return "generic";
}

export default function RouteErrorBoundary() {
  const { t } = useTranslation();
  const error = useRouteError();
  const key = resolveErrorKey(error);

  return (
    <div className="system-page-shell">
      <div className="system-page-card">
        <small>{t(`systemPages.${key}.eyebrow`)}</small>
        <h1>{t(`systemPages.${key}.title`)}</h1>
        <p>{t(`systemPages.${key}.body`)}</p>
        <div className="system-page-actions">
          <Link className="cta" to="/">
            {t("systemPages.actions.home")}
          </Link>
          <Link className="ghost-button" to="/account">
            {t("systemPages.actions.account")}
          </Link>
        </div>
      </div>
    </div>
  );
}
