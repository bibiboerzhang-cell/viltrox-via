/**
 * Viltrox Marketing — Standalone Login Route
 * Path: /login
 *
 * 这个页面不再嵌在 AdminRoute 的 "未登录态" 分支里。
 * 独立一个路由:
 * Viltrox Marketing 使用同一个登录入口。
 */
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { useLocale } from "../../app/providers/LocaleProvider";
import { useAuth } from "../../hooks/useAuth";
import { NEXT_QUERY_KEY, consumeSessionExpiredNotice, sanitizeNextPath } from "../../lib/authSession";
import { frontendBuildInfo, shortBuildSha } from "../../lib/buildInfo";
import { PUBLIC_SURFACE_NAME } from "../../lib/publicSurface";
import { ThemeSwitch } from "../../shared/ThemeSwitch";
import "../../styles/admin.css";

const SESSION_EXPIRED_NOTICE_STYLE = {
  border: "1px solid color-mix(in srgb, var(--ds-warn) 35%, transparent)",
  borderRadius: "var(--ds-radius-md)",
  padding: "10px 12px",
  background: "var(--ds-warn-soft)",
  color: "var(--ds-warn)",
  fontSize: 13,
  marginBottom: 14,
} as const;

export default function AdminLoginRoute() {
  const { status, user, signIn } = useAuth();
  const { t } = useLocale();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // U-B3:会话失效跳回来时带 ?next=原地址(只认站内相对路径),登录后回到原页面;
  // 「登录已失效」提示读一次即清,只在回到登录页的第一帧出现一次。
  const nextPath = sanitizeNextPath(searchParams.get(NEXT_QUERY_KEY));
  const [sessionExpired] = useState(() => consumeSessionExpiredNotice());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (status === "authenticated" && user) {
      navigate(nextPath, { replace: true });
    }
  }, [status, user, navigate, nextPath]);

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
      navigate(nextPath, { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg || "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  if (status === "authenticated" && user) {
    return <Navigate to={nextPath} replace />;
  }

  return (
    <div className="admin-auth-viewport">
      <div style={{ position: "absolute", top: 18, right: 18, zIndex: 2 }}>
        <ThemeSwitch />
      </div>
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
              {PUBLIC_SURFACE_NAME}
            </div>
          </div>
        </div>

        <h1 className="admin-auth-card__title">
          {t("登录")}
        </h1>

        {sessionExpired && !error ? (
          <div id="ax-login-session-expired" role="status" aria-live="polite" style={SESSION_EXPIRED_NOTICE_STYLE}>
            {t("登录已失效，请重新登录。")}
          </div>
        ) : null}

        {error ? (
          <div id="ax-login-error" className="admin-auth-card__error" role="alert" aria-live="polite">
            {t(error)}
          </div>
        ) : null}

        <form className="admin-auth-card__form" onSubmit={handleSubmit}>
          <label className="admin-auth-card__field" htmlFor="ax-login-email">
            <span>{t("邮箱")}</span>
            <input
              id="ax-login-email"
              name="username"
              type="text"
              inputMode="email"
              autoCapitalize="none"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "ax-login-error" : undefined}
              autoFocus
              disabled={submitting}
            />
          </label>

          <label className="admin-auth-card__field" htmlFor="ax-login-password">
            <span>{t("密码")}</span>
            <input
              id="ax-login-password"
              name="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "ax-login-error" : undefined}
              disabled={submitting}
            />
          </label>

          <button
            type="submit"
            className="admin-auth-card__primary"
            disabled={submitting}
            aria-busy={submitting}
          >
            {submitting ? t("登录中…") : t("登录")}
          </button>
        </form>

        <div
          className="admin-auth-card__version"
          title={`${frontendBuildInfo.gitBranch} · ${frontendBuildInfo.gitSha} · built ${frontendBuildInfo.builtAt}`}
        >
          {t("前端版本 {version}", { version: shortBuildSha(frontendBuildInfo.gitSha) })}
        </div>
      </div>
    </div>
  );
}
