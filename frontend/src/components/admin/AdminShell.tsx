/**
 * V-OS Admin — Shell (v2 with mobile support)
 *
 * Desktop: topbar + sidebar (9 tabs) + main outlet
 * Mobile:  topbar (with hamburger) + off-canvas drawer + bottom tab bar (5 primary tabs)
 */
import { useMemo, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../../hooks/useAuth";
import { Icons, type IconName } from "./Icons";

interface NavEntry {
  key: string;
  labelKey: string;
  fallback: string;
  icon: IconName;
  to: string;
  primary?: boolean; // shown in bottom tab bar on mobile
}

const NAV_ENTRIES: NavEntry[] = [
  { key: "overview",   labelKey: "admin.nav.overview",   fallback: "Overview",   icon: "layout",    to: "/admin", primary: true },
  { key: "operations", labelKey: "admin.nav.operations", fallback: "Operations", icon: "ops",       to: "/admin/operations", primary: true },
  { key: "creators",   labelKey: "admin.nav.creators",   fallback: "Creators",   icon: "users",     to: "/admin/creators", primary: true },
  { key: "products",   labelKey: "admin.nav.products",   fallback: "Products",   icon: "products",  to: "/admin/products" },
  { key: "analytics",  labelKey: "admin.nav.analytics",  fallback: "Analytics",  icon: "analytics", to: "/admin/analytics", primary: true },
  { key: "student",    labelKey: "admin.nav.student",    fallback: "Student",    icon: "student",   to: "/admin/student" },
  { key: "via",        labelKey: "admin.nav.via",        fallback: "Via",        icon: "via",       to: "/admin/via" },
  { key: "command",    labelKey: "admin.nav.command",    fallback: "Command",    icon: "command",   to: "/admin/command" },
  { key: "runtime",    labelKey: "admin.nav.runtime",    fallback: "Runtime",    icon: "runtime",   to: "/admin/runtime", primary: true },
  { key: "intelligence", labelKey: "admin.nav.intelligence", fallback: "Intelligence", icon: "analytics", to: "/admin/intelligence" },
  { key: "deepsight", labelKey: "admin.nav.deepsight", fallback: "DeepSight", icon: "via", to: "/admin/deepsight" },
  { key: "system", labelKey: "admin.nav.system", fallback: "System", icon: "command", to: "/admin/system" },
  { key: "kol_ops", labelKey: "admin.nav.kol_ops", fallback: "KOL Ops", icon: "users", to: "/admin/kol-ops" },
  { key: "insights", labelKey: "admin.nav.insights", fallback: "Insights", icon: "analytics", to: "/admin/insights" },
];

const PRIMARY_TABS = NAV_ENTRIES.filter((e) => e.primary);

const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};
const configuredPublicHome = String(env.VITE_PUBLIC_SITE_URL ?? "").trim().replace(/\/+$/, "");

function resolvePublicHomeHref(): string {
  if (configuredPublicHome) return `${configuredPublicHome}/`;
  if (typeof window === "undefined") return "/";

  const localPublicPorts: Record<string, string> = {
    "8002": "8001",
    "8102": "8101",
  };
  const publicPort = localPublicPorts[window.location.port];

  if (publicPort) {
    return `${window.location.protocol}//${window.location.hostname}:${publicPort}/`;
  }

  return "/";
}

function canReadTab(user: unknown, key: string): boolean {
  const u = user as { role?: string; is_owner?: boolean; permissions?: Record<string, string> } | null;
  if (!u) return false;
  if (u.is_owner) return true;
  const permissions = u.permissions || {};
  if (Object.keys(permissions).length === 0) {
    return String(u.role || "").toLowerCase() === "admin";
  }
  return ["read", "write"].includes(String(permissions[key] || "none").toLowerCase());
}

interface AdminShellProps {
  children: ReactNode;
  activeKey?: string;
  badges?: Partial<Record<string, number>>;
  onSearch?: (query: string) => void;
}

export function AdminShell({ children, activeKey, badges, onSearch }: AdminShellProps) {
  const { t, i18n } = useTranslation();
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchValue, setSearchValue] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const publicHomeHref = useMemo(resolvePublicHomeHref, []);

  const resolvedActiveKey = useMemo(() => {
    if (activeKey) return activeKey;
    const path = location.pathname;
    if (path === "/admin" || path === "/admin/") return "overview";
    const match = NAV_ENTRIES.find((n) => n.to !== "/admin" && path.startsWith(n.to));
    return match?.key ?? "overview";
  }, [activeKey, location.pathname]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (onSearch && searchValue.trim()) {
      onSearch(searchValue.trim());
    } else if (searchValue.trim()) {
      navigate(`/admin/operations?section=users&q=${encodeURIComponent(searchValue.trim())}`);
    }
  };

  const toggleLang = () => {
    const next = i18n.language?.startsWith("zh") ? "en" : "zh";
    void i18n.changeLanguage(next);
  };

  const handleLogout = async () => {
    try {
      await signOut();
    } finally {
      navigate("/admin/login", { replace: true });
    }
  };

  const userInitial = (user?.name || user?.email || "A").trim().charAt(0).toUpperCase();
  const langLabel = i18n.language?.startsWith("zh") ? "中" : "EN";
  const visibleNavEntries = NAV_ENTRIES.filter((entry) => canReadTab(user, entry.key));
  const visiblePrimaryTabs = PRIMARY_TABS.filter((entry) => canReadTab(user, entry.key));

  return (
    <div className={`admin-root${drawerOpen ? " is-drawer-open" : ""}`}>
      {/* ─── Top bar ─── */}
      <header className="admin-root__topbar" role="banner">
        <button
          type="button"
          className="admin-root__topbar-link admin-root__hamburger"
          onClick={() => setDrawerOpen((v) => !v)}
          aria-label="菜单"
          style={{ display: "none" }}
        >
          <Icons.menu />
        </button>

        <Link to="/admin" className="admin-root__brand" aria-label="V-OS Admin home">
          <span className="admin-root__mark">V</span>
          <span className="admin-root__title">V-OS Admin</span>
        </Link>

        <form
          className="admin-root__search"
          onSubmit={handleSearchSubmit}
          role="search"
        >
          <Icons.search aria-hidden />
          <input
            type="text"
            placeholder={t("admin.shell.searchPlaceholder", "搜索 VID / handle / 邮箱…")}
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value)}
            aria-label={t("admin.shell.searchLabel", "全局搜索")}
          />
        </form>

        <nav className="admin-root__topbar-actions">
          <a
            className="admin-root__topbar-link"
            href={publicHomeHref}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={t("admin.shell.publicSite", "公开站")}
          >
            <Icons.externalLink />
            <span style={{ display: "inline" }} className="admin-root__topbar-action-label">
              {t("admin.shell.publicSite", "公开站")}
            </span>
          </a>
          <button
            type="button"
            className="admin-root__topbar-link"
            onClick={toggleLang}
            aria-label={t("admin.shell.toggleLanguage", "切换语言")}
          >
            {langLabel}
          </button>
          <button
            type="button"
            className="admin-root__avatar"
            onClick={handleLogout}
            aria-label={t("admin.shell.logout", "退出")}
            title={user?.email || ""}
          >
            {userInitial}
          </button>
        </nav>
      </header>

      {/* Drawer backdrop (mobile) */}
      <div
        className="admin-root__drawer-backdrop"
        onClick={() => setDrawerOpen(false)}
      />

      {/* ─── Body: sidebar + main ─── */}
      <div className="admin-root__body">
        <aside className="admin-root__sidebar" role="navigation">
          {visibleNavEntries.map((entry) => {
            const Icon = Icons[entry.icon];
            const isActive = entry.key === resolvedActiveKey;
            const badge = badges?.[entry.key];
            return (
              <NavLink
                key={entry.key}
                to={entry.to}
                end={entry.to === "/admin"}
                className={`admin-root__nav-item${isActive ? " is-active" : ""}`}
                onClick={() => setDrawerOpen(false)}
              >
                <Icon aria-hidden />
                <span>{t(entry.labelKey, entry.fallback)}</span>
                {badge && badge > 0 ? (
                  <span className="admin-root__nav-badge ax-num">{badge}</span>
                ) : null}
              </NavLink>
            );
          })}
        </aside>

        <main className="admin-root__main" role="main">
          {children}
        </main>
      </div>

      {/* Mobile bottom tab bar (hidden on desktop via CSS) */}
      <nav className="admin-root__tabbar" role="navigation" aria-label="主导航">
        {visiblePrimaryTabs.map((entry) => {
          const Icon = Icons[entry.icon];
          const isActive = entry.key === resolvedActiveKey;
          const badge = badges?.[entry.key];
          return (
            <button
              key={entry.key}
              type="button"
              className={`admin-root__tabbar-item${isActive ? " is-active" : ""}`}
              onClick={() => navigate(entry.to)}
            >
              <Icon />
              <span>{t(entry.labelKey, entry.fallback)}</span>
              {badge && badge > 0 ? (
                <span className="admin-root__tabbar-badge">{badge}</span>
              ) : null}
            </button>
          );
        })}
      </nav>
    </div>
  );
}

export default AdminShell;
