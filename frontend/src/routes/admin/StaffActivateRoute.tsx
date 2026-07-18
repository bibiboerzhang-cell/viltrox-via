import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { acceptStaffInvite, getStaffInviteStatus } from "../../domains/settings";
import { frontendBuildInfo, shortBuildSha } from "../../lib/buildInfo";
import { PUBLIC_SURFACE_NAME } from "../../lib/publicSurface";
import "../../styles/admin.css";

export default function StaffActivateRoute() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const inviteToken = searchParams.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(inviteToken ? null : "激活链接缺少 token");
  const [status, setStatus] = useState<"checking" | "active" | "used" | "expired" | "invalid">(inviteToken ? "checking" : "invalid");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!inviteToken) {
      setStatus("invalid");
      setError("激活链接缺少 token");
      return;
    }
    setStatus("checking");
    setError(null);
    getStaffInviteStatus(inviteToken)
      .then((response) => {
        if (cancelled) return;
        const nextState = String(response.state || (response.valid ? "active" : "invalid"));
        if (response.valid && nextState === "active") {
          setStatus("active");
          return;
        }
        const mapped = nextState === "used" || nextState === "expired" ? nextState : "invalid";
        setStatus(mapped);
        setError(response.message || "激活链接无效");
      })
      .catch((err) => {
        if (cancelled) return;
        setStatus("invalid");
        setError(err instanceof Error ? err.message : "激活链接校验失败");
      });
    return () => {
      cancelled = true;
    };
  }, [inviteToken]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!inviteToken) {
      setError("激活链接无效");
      return;
    }
    if (password.length < 8) {
      setError("密码至少 8 位");
      return;
    }
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      await acceptStaffInvite(inviteToken, password);
      setDone(true);
      setStatus("used");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message || "激活失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="admin-auth-viewport">
      <div className="admin-auth-card" role="main">
        <div className="admin-auth-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>
              {PUBLIC_SURFACE_NAME}
            </div>
          </div>
        </div>

        <h1 className="admin-auth-card__title">激活账号</h1>

        {done ? (
          <>
            <div className="admin-auth-card__success" role="status" aria-live="polite">账号已激活，可以登录。</div>
            <button className="admin-auth-card__primary" type="button" onClick={() => navigate("/login", { replace: true })}>
              去登录
            </button>
          </>
        ) : status === "checking" ? (
          <div className="admin-auth-card__success" role="status" aria-live="polite">正在校验激活链接...</div>
        ) : status !== "active" ? (
          <>
            <div className="admin-auth-card__error" role="alert" aria-live="polite">
              {status === "used" ? "这个激活链接已经使用过。" : status === "expired" ? "这个激活链接已过期。" : error || "激活链接无效。"}
            </div>
            <Link className="admin-auth-card__back" to="/login">返回登录</Link>
          </>
        ) : (
          <>
            {error ? <div className="admin-auth-card__error" role="alert" aria-live="polite">{error}</div> : null}
            <form className="admin-auth-card__form" onSubmit={handleSubmit}>
              <label className="admin-auth-card__field" htmlFor="staff-activate-password">
                <span>设置密码</span>
                <input
                  id="staff-activate-password"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="new-password"
                  disabled={submitting || !inviteToken}
                />
              </label>
              <label className="admin-auth-card__field" htmlFor="staff-activate-confirm">
                <span>确认密码</span>
                <input
                  id="staff-activate-confirm"
                  type="password"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  autoComplete="new-password"
                  disabled={submitting || !inviteToken}
                />
              </label>
              <button className="admin-auth-card__primary" type="submit" disabled={submitting || !inviteToken}>
                {submitting ? "激活中..." : "激活账号"}
              </button>
            </form>
            <Link className="admin-auth-card__back" to="/login">返回登录</Link>
          </>
        )}

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
