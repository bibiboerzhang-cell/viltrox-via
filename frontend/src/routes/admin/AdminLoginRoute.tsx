/**
 * V-OS Admin — Standalone Login Route
 * Path: /admin/login
 *
 * 这个页面不再嵌在 AdminRoute 的 "未登录态" 分支里。
 * 独立一个路由:
 *   - /admin        需要 admin 登录, 未登录 → Navigate to /admin/login
 *   - /admin/login  登录页本身, 已登录 → Navigate to /admin
 */
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../../hooks/useAuth";
import "../../styles/admin.css";

export default function AdminLoginRoute() {
  const { t } = useTranslation();
  const { status, user, signIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // 已经登录且是 admin → 直接去 admin
  useEffect(() => {
    if (status === "authenticated" && user?.role === "admin") {
      navigate("/admin", { replace: true });
    }
  }, [status, user, navigate]);

  // 如果是普通用户登录着, 提示要 admin 权限
  const alreadySignedInAsNonAdmin =
    status === "authenticated" && user && user.role !== "admin";

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError(t("admin.login.errorMissing", "请输入邮箱和密码"));
      return;
    }
    setSubmitting(true);
    try {
      await signIn(email.trim(), password);
      // signIn 成功后 useEffect 会跳; 这里给个兜底
      navigate("/admin", { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg || t("admin.login.errorGeneric", "登录失败"));
    } finally {
      setSubmitting(false);
    }
  };

  // 真实 admin 已登录 → 重定向 (避免 flash)
  if (status === "authenticated" && user?.role === "admin") {
    return <Navigate to="/admin" replace />;
  }

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
              {t("admin.login.tagline", "Viltrox Operations")}
            </div>
          </div>
        </div>

        <h1 className="admin-auth-card__title">
          {t("admin.login.title", "管理员登录")}
        </h1>
        <p className="admin-auth-card__subtitle">
          {t("admin.login.subtitle", "使用你的 admin 账号进入控制台")}
        </p>

        {alreadySignedInAsNonAdmin ? (
          <div className="admin-auth-card__error">
            {t(
              "admin.login.notAdmin",
              "当前账号无管理员权限。请先退出,再用 admin 账号登录。",
            )}
          </div>
        ) : null}

        {error ? <div className="admin-auth-card__error">{error}</div> : null}

        <form onSubmit={handleSubmit}>
          <label className="admin-auth-card__label" htmlFor="ax-login-email">
            {t("admin.login.email", "邮箱")}
          </label>
          <input
            id="ax-login-email"
            type="email"
            className="admin-auth-card__input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            autoFocus
            disabled={submitting}
          />

          <label className="admin-auth-card__label" htmlFor="ax-login-password">
            {t("admin.login.password", "密码")}
          </label>
          <input
            id="ax-login-password"
            type="password"
            className="admin-auth-card__input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            disabled={submitting}
          />

          <button
            type="submit"
            className="admin-auth-card__primary"
            disabled={submitting}
          >
            {submitting
              ? t("admin.login.submitting", "登录中…")
              : t("admin.login.submit", "登录")}
          </button>
        </form>

        <a href="/" className="admin-auth-card__back">
          ← {t("admin.login.back", "回到公开首页")}
        </a>
      </div>
    </div>
  );
}
