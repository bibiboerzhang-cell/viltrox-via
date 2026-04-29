import type { CSSProperties, PropsWithChildren, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { LANGUAGE_STORAGE_KEY } from "../i18n";
import { FloatingViaCat } from "./catographer/FloatingViaCat";
import type { AuthUser } from "../lib/api";

interface NavItem {
  to: string;
  label: string;
}

interface AppShellProps extends PropsWithChildren {
  tone?: "light" | "dark";
  eyebrow?: string;
  title: string;
  subtitle?: string;
  navItems: NavItem[];
  brandFooter?: ReactNode;
  centerContent?: ReactNode;
  actions?: ReactNode;
  shellClassName?: string;
  showFloatingVia?: boolean;
}

interface PanelProps extends PropsWithChildren {
  title: string;
  kicker?: string;
  aside?: ReactNode;
}

interface MetricItem {
  label: string;
  value: string;
  note?: string;
}

export function AppShell({
  tone = "light",
  eyebrow,
  title,
  subtitle,
  navItems,
  brandFooter,
  centerContent,
  actions,
  shellClassName,
  showFloatingVia = true,
  children,
}: AppShellProps) {
  return (
    <div className={`shell ui-shell ui-shell--${tone}${shellClassName ? ` ${shellClassName}` : ""}`}>
      <header className="topbar">
        <div className="topbar-brand-stack">
          <Link className="brand" to="/">
            <span className="brand-mark">V</span>
            <span className="brand-copy">
              <strong>Viltrox Creator Program</strong>
              {brandFooter ? <div className="brand-copy__subrow">{brandFooter}</div> : <span>VILTROX</span>}
            </span>
          </Link>
        </div>
        <div className="seg" aria-label="Primary navigation">
          {centerContent ? (
            centerContent
          ) : (
            <nav className="ui-nav__rail" aria-label="Primary navigation">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `seg-link${isActive ? " active" : ""}`}
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          )}
        </div>
        {actions ? <div className="ui-shell__actions">{actions}</div> : null}
      </header>

      <section className="section-head">
        <div className="copy">
          {eyebrow ? <small>{eyebrow}</small> : null}
          <h1>{title}</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </section>

      <main>{children}</main>
      {showFloatingVia ? <FloatingViaCat /> : null}
    </div>
  );
}

export function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const currentLanguage = i18n.resolvedLanguage?.toLowerCase().startsWith("zh") ? "zh" : "en";

  function changeLanguage(nextLanguage: "en" | "zh") {
    void i18n.changeLanguage(nextLanguage);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
    }
  }

  return (
    <div className="language-toggle" aria-label="Language switch">
      <button
        type="button"
        className={currentLanguage === "en" ? "is-active" : ""}
        onClick={() => changeLanguage("en")}
      >
        {t("nav.languageEn")}
      </button>
      <button
        type="button"
        className={currentLanguage === "zh" ? "is-active" : ""}
        onClick={() => changeLanguage("zh")}
      >
        {t("nav.languageZh")}
      </button>
    </div>
  );
}

export function Panel({ title, kicker, aside, children }: PanelProps) {
  return (
    <section className="ui-panel">
      <div className="ui-panel__header">
        <div>
          {kicker ? <p className="ui-panel__kicker">{kicker}</p> : null}
          <h2 className="ui-panel__title">{title}</h2>
        </div>
        {aside ? <div className="ui-panel__aside">{aside}</div> : null}
      </div>
      <div className="ui-panel__body">{children}</div>
    </section>
  );
}

export function MetricStrip({ items, columns }: { items: MetricItem[]; columns?: number }) {
  return (
    <div
      className="metric-strip"
      style={{ ["--metric-columns" as string]: String(columns ?? items.length ?? 4) } as CSSProperties}
    >
      {items.map((item) => (
        <article key={item.label} className="metric-card">
          <span className="metric-card__label">{item.label}</span>
          <strong className="metric-card__value">{item.value}</strong>
          {item.note ? <span className="metric-card__note">{item.note}</span> : null}
        </article>
      ))}
    </div>
  );
}

export function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  return <span className={`status-pill status-pill--${tone}`}>{label}</span>;
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{body}</p>
      {action ? <div className="empty-state__action">{action}</div> : null}
    </div>
  );
}

export function TopAccountPill({
  user,
  signedInLabel,
}: {
  user: AuthUser | null;
  signedInLabel?: string;
}) {
  const { openAuthModal } = useAuth();
  const { t } = useTranslation();
  const displayName = user?.name?.trim() || user?.email?.split("@")[0] || "My account";
  const resolvedSignedInLabel = signedInLabel || t("account.signedIn");
  return user ? (
    <Link className="acct top-account-card" to="/account">
      <span className="avatar top-account-card__mark">↗</span>
      <span className="meta top-account-card__copy">
        <small>{resolvedSignedInLabel}</small>
        <strong>{displayName}</strong>
      </span>
    </Link>
  ) : (
    <button className="acct top-account-card top-account-card--button" type="button" onClick={() => openAuthModal("signin")}>
      <span className="avatar top-account-card__mark">↗</span>
      <span className="meta top-account-card__copy">
        <small>{t("login.nav.account")}</small>
        <strong>{t("account.signInRequiredAction")}</strong>
      </span>
    </button>
  );
}

function bwAccountInitials(user: AuthUser | null) {
  const source = user?.name?.trim() || user?.email?.trim() || "V";
  return (
    source
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() || "")
      .join("") || "V"
  );
}

function bwGuestAccountMark() {
  return ">";
}

export function BwTopNav({
  active,
  user,
  points,
}: {
  active: "upload" | "rewards" | "account";
  user: AuthUser | null;
  points?: number | null;
}) {
  const { openAuthModal } = useAuth();
  const { t, i18n } = useTranslation();
  const currentLanguage = i18n.resolvedLanguage?.toLowerCase().startsWith("zh") ? "zh" : "en";

  function changeLanguage(nextLanguage: "en" | "zh") {
    void i18n.changeLanguage(nextLanguage);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
    }
  }

  return (
    <header className="bw-topnav">
      <Link className="bw-topnav__brand" to="/">
        VILTROX
      </Link>
      <nav className="bw-topnav__rail" aria-label="Primary navigation">
        <NavLink className={() => `bw-topnav__pill${active === "upload" ? " is-active" : ""}`} to="/" end>
          {t("login.nav.upload")}
        </NavLink>
        <NavLink className={() => `bw-topnav__pill${active === "rewards" ? " is-active" : ""}`} to="/redeem">
          {t("login.nav.redeem")}
        </NavLink>
        <NavLink className={() => `bw-topnav__pill${active === "account" ? " is-active" : ""}`} to="/account">
          {t("login.nav.account")}
        </NavLink>
      </nav>
      <div className="bw-topnav__side">
        {typeof points === "number" ? <div className="bw-topnav__points">{points.toLocaleString()} {t("redeem.pointsShort")}</div> : null}
        <div className="bw-topnav__lang" aria-label="Language switch">
          <button
            type="button"
            className={currentLanguage === "en" ? "is-active" : ""}
            onClick={() => changeLanguage("en")}
          >
            {t("nav.languageEn")}
          </button>
          <button
            type="button"
            className={currentLanguage === "zh" ? "is-active" : ""}
            onClick={() => changeLanguage("zh")}
          >
            {t("nav.languageZh")}
          </button>
        </div>
        {user ? (
          <Link className="bw-topnav__account" to="/account">
            <span className="bw-topnav__avatar">{bwAccountInitials(user)}</span>
          </Link>
        ) : (
          <button
            className="bw-topnav__account bw-topnav__account--button"
            type="button"
            onClick={() => openAuthModal("signin")}
            aria-label={t("account.signInRequiredAction")}
            title={t("account.signInRequiredAction")}
          >
            <span className="bw-topnav__avatar">{bwGuestAccountMark()}</span>
          </button>
        )}
      </div>
    </header>
  );
}
