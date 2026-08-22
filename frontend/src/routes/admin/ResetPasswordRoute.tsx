import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useLocale } from "../../app/providers/LocaleProvider";
import { resetPassword } from "../../services/vkpi/staff-api";
import { frontendBuildInfo, shortBuildSha } from "../../lib/buildInfo";
import { PUBLIC_SURFACE_NAME } from "../../lib/publicSurface";
import { ThemeSwitch } from "../../shared/ThemeSwitch";
import "../../styles/admin.css";

// 2026-06-16:修复改密码流程 —— 重置链接 `?reset_token=` 此前前端无路由消费,点了落登录页。
// 这里读 reset_token(兼容 token)→ 设新密码 → 调 /api/auth/reset-password。
export default function ResetPasswordRoute() {
  const { t } = useLocale();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const resetToken = searchParams.get("reset_token") || searchParams.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(resetToken ? null : "重置链接缺少 token");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!resetToken) { setError("重置链接无效"); return; }
    if (password.length < 8) { setError("密码至少 8 位"); return; }
    if (password !== confirmPassword) { setError("两次输入的密码不一致"); return; }
    setSubmitting(true);
    try {
      const res = (await resetPassword(resetToken, password)) as { status?: string; message?: string };
      if (res && res.status === "error") {
        setError(res.message || "重置失败,链接可能已过期");
        return;
      }
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重置失败,链接可能已过期或无效");
    } finally {
      setSubmitting(false);
    }
  };

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

        <h1 className="admin-auth-card__title">{t("重置密码")}</h1>

        {done ? (
          <>
            <div className="admin-auth-card__success" role="status" aria-live="polite">
              {t("密码已重置，可以登录。")}
            </div>
            <button className="admin-auth-card__primary" type="button" onClick={() => navigate("/login", { replace: true })}>
              {t("去登录")}
            </button>
          </>
        ) : !resetToken ? (
          <>
            <div className="admin-auth-card__error" role="alert" aria-live="polite">
              {t(error || "重置链接无效。")}
            </div>
            <Link className="admin-auth-card__back" to="/login">{t("返回登录")}</Link>
          </>
        ) : (
          <>
            {error ? <div className="admin-auth-card__error" role="alert" aria-live="polite">{t(error)}</div> : null}
            <form className="admin-auth-card__form" onSubmit={handleSubmit}>
              <label className="admin-auth-card__field" htmlFor="reset-password">
                <span>{t("设置新密码")}</span>
                <input
                  id="reset-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  disabled={submitting}
                />
              </label>
              <label className="admin-auth-card__field" htmlFor="reset-confirm">
                <span>{t("确认新密码")}</span>
                <input
                  id="reset-confirm"
                  type="password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  autoComplete="new-password"
                  disabled={submitting}
                />
              </label>
              <button className="admin-auth-card__primary" type="submit" disabled={submitting}>
                {submitting ? t("重置中...") : t("重置密码")}
              </button>
            </form>
            <Link className="admin-auth-card__back" to="/login">{t("返回登录")}</Link>
          </>
        )}

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
