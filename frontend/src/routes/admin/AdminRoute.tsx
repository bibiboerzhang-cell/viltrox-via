/**
 * Viltrox Marketing — authenticated app gate.
 *
 * Responsibility:
 *   - Root path `/` is the Viltrox Marketing system.
 *   - Anonymous users see the shared login.
 *   - Signed-in users enter a role-scoped Marketing view.
 */
import { useEffect, useState } from "react";
import { VkpiTab } from "../../components/admin/tabs_v2/VkpiTab";
import { VkpiDashboard } from "../../components/vkpi";
import { GlassDemoPage } from "../../components/vkpi/pages/GlassDemoPage";
import { useAuth } from "../../hooks/useAuth";
import AdminLoginRoute from "./AdminLoginRoute";
import "../../styles/admin.css";

const importMetaEnv = (import.meta as { env?: { DEV?: boolean } }).env;

function getHashPage(): string {
  if (typeof window === "undefined") return "";
  return window.location.hash.replace(/^#\/?/, "");
}

function AdminAuthLoading() {
  return (
    <div className="admin-auth-viewport">
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Viltrox Marketing
            </div>
            <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>
              正在恢复登录会话
            </div>
          </div>
        </div>
        <h1 className="admin-auth-card__title">
          正在进入 Viltrox Marketing
        </h1>
        <p className="admin-auth-card__subtitle">
          请稍等，正在校验本地登录态。
        </p>
      </div>
    </div>
  );
}

function hasMarketingPermission(user: { role?: string; is_owner?: boolean; permissions?: Record<string, string> }): boolean {
  if (user.is_owner) return true;
  if (String(user.role || "").toLowerCase() === "admin") return true;
  const permissions = user.permissions || {};
  if (Object.keys(permissions).length === 0) {
    return false;
  }
  return ["read", "write"].includes(String(permissions.vkpi || permissions.marketing || "none").toLowerCase());
}

function isGlassDemoRequest(hashPage: string): boolean {
  if (!importMetaEnv?.DEV) return false;
  return hashPage === "glass-demo";
}

export default function AdminRoute() {
  const { status, token, user, signOut } = useAuth();
  const [hashPage, setHashPage] = useState(() => getHashPage());

  useEffect(() => {
    const handleHashChange = () => setHashPage(getHashPage());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  if (isGlassDemoRequest(hashPage)) {
    return <GlassDemoPage userName="Jianbo" userRole="Marketing Director" />;
  }

  if (status === "loading") {
    return <AdminAuthLoading />;
  }

  // ── Not signed in → shared login route ──
  if (status !== "authenticated" || !user) {
    return <AdminLoginRoute />;
  }

  // ── Signed in but has no internal Marketing permission → access-denied card ──
  if (!hasMarketingPermission(user)) {
    return (
      <div className="admin-auth-viewport">
        <div className="admin-auth-card">
          <div className="admin-auth-card__brand">
            <span className="admin-root__mark">V</span>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
                Viltrox Marketing
              </div>
              <div style={{ fontSize: 11, color: "var(--ax-text-2)" }}>
                访问被拒绝
              </div>
            </div>
          </div>
          <h1 className="admin-auth-card__title">
            当前账号没有内部系统权限
          </h1>
          <p className="admin-auth-card__subtitle">
            你已登录，但当前账号没有 Viltrox Marketing 权限。
          </p>
          <button
            type="button"
            className="admin-auth-card__primary"
            onClick={() => {
              void signOut();
            }}
          >
            退出当前账号
          </button>
          <a href="/" className="admin-auth-card__back">
            ← 返回登录页
          </a>
        </div>
      </div>
    );
  }

  // ── Signed-in user → role-scoped Marketing shell ──
  const tokenStr = token ?? "";
  return <VkpiTab token={tokenStr} user={user} onSignOut={signOut} />;
}
