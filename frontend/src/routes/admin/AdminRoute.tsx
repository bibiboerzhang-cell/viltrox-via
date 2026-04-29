/**
 * V-OS Admin — AdminRoute (gate + tab router)
 *
 * Responsibility:
 *   - Authentication gate: not signed in → /admin/login; non-admin → denied card
 *   - Admin-authenticated → AdminShell + nested routes for all 9 tabs
 *
 * Tab mapping:
 *   /admin              → OverviewTab
 *   /admin/operations   → OperationsTab
 *   /admin/creators     → CreatorsTab (HERO)
 *   /admin/products     → ProductsTab
 *   /admin/analytics    → AnalyticsTab
 *   /admin/student      → StudentTab
 *   /admin/via          → ViaTab
 *   /admin/command      → CommandTab
 *   /admin/runtime      → RuntimeTab
 */
import { Navigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { AdminShell } from "../../components/admin/AdminShell";
import {
  AnalyticsTab,
  CommandTab,
  CreatorsTab,
  DeepSightTab,
  IntelligenceTab,
  InsightsTab,
  KolOpsTab,
  OperationsTab,
  OverviewTab,
  ProductsTab,
  RuntimeTab,
  StudentTab,
  SystemTab,
  ViaTab,
} from "../../components/admin/tabs_v2";
import { useAuth } from "../../hooks/useAuth";
import "../../styles/admin.css";

function AdminAuthLoading() {
  const { t } = useTranslation();
  return (
    <div className="admin-auth-viewport">
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
              V-OS Admin
            </div>
            <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>
              {t("admin.shell.loadingSession", "正在恢复管理员会话")}
            </div>
          </div>
        </div>
        <h1 className="admin-auth-card__title">
          {t("admin.shell.loadingTitle", "正在进入控制台")}
        </h1>
        <p className="admin-auth-card__subtitle">
          {t("admin.shell.loadingSubtitle", "请稍等，正在校验本地登录态。")}
        </p>
      </div>
    </div>
  );
}

function hasTabPermission(user: { role?: string; is_owner?: boolean; permissions?: Record<string, string> }, tabKey: string): boolean {
  if (user.is_owner) return true;
  const permissions = user.permissions || {};
  if (Object.keys(permissions).length === 0) {
    return String(user.role || "").toLowerCase() === "admin";
  }
  return ["read", "write"].includes(String(permissions[tabKey] || "none").toLowerCase());
}

function NoPermissionCard() {
  const { t } = useTranslation();
  return (
    <div style={{ padding: 16 }}>
      <div className="ax-card">
        <h2 style={{ marginTop: 0 }}>{t("admin.permissions.no_access", "您没有此板块的访问权限")}</h2>
        <p style={{ color: "var(--ax-text-2)", marginBottom: 0 }}>
          {t("admin.permissions.contact_owner", "请联系 owner 申请权限。")}
        </p>
      </div>
    </div>
  );
}

export default function AdminRoute() {
  const { t } = useTranslation();
  const { status, token, user, signOut } = useAuth();
  const location = useLocation();

  const isAdmin = String(user?.role || "").toLowerCase() === "admin";

  if (status === "loading") {
    return <AdminAuthLoading />;
  }

  // ── Not signed in → redirect to dedicated login route ──
  if (status !== "authenticated" || !user) {
    const from = location.pathname + location.search;
    return (
      <Navigate
        to={`/admin/login${from && from !== "/admin/login" ? `?from=${encodeURIComponent(from)}` : ""}`}
        replace
      />
    );
  }

  // ── Signed in but not admin → access-denied card ──
  if (!isAdmin) {
    return (
      <div className="admin-auth-viewport">
        <div className="admin-auth-card">
          <div className="admin-auth-card__brand">
            <span className="admin-root__mark">V</span>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
                V-OS Admin
              </div>
              <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>
                {t("admin.denied.badge", "Access denied")}
              </div>
            </div>
          </div>
          <h1 className="admin-auth-card__title">
            {t("admin.denied.title", "需要管理员权限")}
          </h1>
          <p className="admin-auth-card__subtitle">
            {t("admin.denied.subtitle", "你已登录,但当前账号没有 admin 权限。")}
          </p>
          <button
            type="button"
            className="admin-auth-card__primary"
            onClick={() => {
              void signOut();
            }}
          >
            {t("admin.denied.signOut", "退出当前账号")}
          </button>
          <a href="/" className="admin-auth-card__back">
            ← {t("admin.denied.back", "回到公开首页")}
          </a>
        </div>
      </div>
    );
  }

  // ── Signed-in admin → shell + tab router ──
  const tokenStr = token ?? "";

  const section = location.pathname.replace(/^\/admin\/?/, "").split("/")[0] || "overview";
  const normalizedSection = section === "kol-ops" ? "kol_ops" : section;
  if (!hasTabPermission(user, normalizedSection)) {
    return (
      <AdminShell activeKey={normalizedSection}>
        <NoPermissionCard />
      </AdminShell>
    );
  }
  const tab = (() => {
    switch (section) {
      case "overview":
        return <OverviewTab token={tokenStr} user={user} />;
      case "operations":
        return <OperationsTab token={tokenStr} user={user} />;
      case "creators":
        return <CreatorsTab token={tokenStr} user={user} />;
      case "products":
        return <ProductsTab token={tokenStr} user={user} />;
      case "analytics":
        return <AnalyticsTab token={tokenStr} user={user} />;
      case "student":
        return <StudentTab token={tokenStr} user={user} />;
      case "via":
        return <ViaTab token={tokenStr} user={user} />;
      case "command":
        return <CommandTab token={tokenStr} user={user} />;
      case "runtime":
        return <RuntimeTab token={tokenStr} user={user} />;
      case "intelligence":
        return <IntelligenceTab token={tokenStr} user={user} />;
      case "deepsight":
        return <DeepSightTab token={tokenStr} user={user} />;
      case "system":
        return <SystemTab token={tokenStr} user={user} />;
      case "kol-ops":
        return <KolOpsTab token={tokenStr} user={user} />;
      case "insights":
        return <InsightsTab token={tokenStr} user={user} />;
      default:
        return <Navigate to="/admin" replace />;
    }
  })();

  return <AdminShell>{tab}</AdminShell>;
}
