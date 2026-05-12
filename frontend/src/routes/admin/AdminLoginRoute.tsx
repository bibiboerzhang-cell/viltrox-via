/**
 * Viltrox Marketing — Standalone Login Route
 * Path: /login
 *
 * 这个页面不再嵌在 AdminRoute 的 "未登录态" 分支里。
 * 独立一个路由:
 * Viltrox Marketing 使用同一个登录入口。
 */
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../../hooks/useAuth";
import { frontendBuildInfo, shortBuildSha } from "../../lib/buildInfo";
import "../../styles/admin.css";

export default function AdminLoginRoute() {
  const { status, user, signIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (status === "authenticated" && user) {
      navigate("/", { replace: true });
    }
  }, [status, user, navigate]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("请输入邮箱和密码");
      return;
    }
    setSubmitting(true);
    try {
      await signIn(email.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg || "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "authenticated" && user) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="admin-auth-viewport">
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
              Viltrox Marketing
            </div>
          </div>
        </div>

        <h1 className="admin-auth-card__title">
          登录
        </h1>

        {error ? <div className="admin-auth-card__error">{error}</div> : null}

        <form className="admin-auth-card__form" onSubmit={handleSubmit}>
          <label className="admin-auth-card__field" htmlFor="ax-login-email">
            <span>邮箱</span>
            <input
              id="ax-login-email"
              type="text"
              inputMode="email"
              autoCapitalize="none"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              autoFocus
              disabled={submitting}
            />
          </label>

          <label className="admin-auth-card__field" htmlFor="ax-login-password">
            <span>密码</span>
            <input
              id="ax-login-password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              disabled={submitting}
            />
          </label>

          <button
            type="submit"
            className="admin-auth-card__primary"
            disabled={submitting}
          >
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>

        <div
          className="admin-auth-card__version"
          title={`${frontendBuildInfo.gitBranch} · ${frontendBuildInfo.gitSha} · built ${frontendBuildInfo.builtAt}`}
        >
          前端版本 {shortBuildSha(frontendBuildInfo.gitSha)}
        </div>
      </div>
    </div>
  );
}
