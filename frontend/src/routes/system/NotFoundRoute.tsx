import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

export default function NotFoundRoute() {
  const { t } = useTranslation();

  return (
    <div className="system-page-shell">
      <div className="system-page-card">
        <small>{t("systemPages.notFound.eyebrow")}</small>
        <h1>{t("systemPages.notFound.title")}</h1>
        <p>{t("systemPages.notFound.body")}</p>
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
