import { FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";

import { AppShell, EmptyState, Panel, StatusPill } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { forgotPassword, register, resendVerification } from "../../services/auth.service";

type AuthMode = "signin" | "register" | "recovery";

function resolveMode(search: string): AuthMode {
  const params = new URLSearchParams(search);
  const mode = params.get("mode");
  if (mode === "register" || mode === "recovery") {
    return mode;
  }
  return "signin";
}

export default function LoginRoute() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const { status, user, signIn } = useAuth();
  const [mode, setMode] = useState<AuthMode>(() => resolveMode(location.search));
  const [message, setMessage] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");

  const authNav = useMemo(
    () => [
      { to: "/", label: t("login.nav.upload") },
      { to: "/redeem", label: t("login.nav.redeem") },
    ],
    [t],
  );

  const copy = useMemo(() => {
    if (mode === "register") {
      return {
        title: t("login.copy.register.title"),
        subtitle: t("login.copy.register.subtitle"),
        action: t("login.copy.register.action"),
      };
    }
    if (mode === "recovery") {
      return {
        title: t("login.copy.recovery.title"),
        subtitle: t("login.copy.recovery.subtitle"),
        action: t("login.copy.recovery.action"),
      };
    }
    return {
      title: t("login.copy.signin.title"),
      subtitle: t("login.copy.signin.subtitle"),
      action: t("login.copy.signin.action"),
    };
  }, [mode, t]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      if (mode === "signin") {
        await signIn(email, password);
        navigate("/account");
        return;
      }
      if (mode === "register") {
        const response = await register(name, email, password);
        setMessage({ tone: "success", body: response.message ?? t("login.messages.registerSuccess") });
        setMode("signin");
        setPassword("");
        return;
      }
      const response = await forgotPassword(email);
      setMessage({ tone: "success", body: response.message ?? t("login.messages.recoverySuccess") });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : t("login.messages.authFailed") });
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!email.trim()) {
      setMessage({ tone: "warning", body: t("login.messages.enterEmailFirst") });
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const response = await resendVerification(email);
      setMessage({ tone: "success", body: response.message ?? t("login.messages.verificationResent") });
    } catch (error) {
      setMessage({ tone: "danger", body: error instanceof Error ? error.message : t("login.messages.verificationResendFailed") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell
      eyebrow={t("login.eyebrow")}
      title={copy.title}
      subtitle={copy.subtitle}
      navItems={authNav}
      actions={status === "authenticated" && user ? <StatusPill label={user.email} tone="success" /> : undefined}
      shellClassName="auth-route-shell"
    >
      <div className="auth-page-grid">
        <Panel title={t("login.session.title")} kicker={t("login.session.kicker")}>
          <div className="auth-mode-row">
            {([
              ["signin", t("login.modes.signin")],
              ["register", t("login.modes.register")],
              ["recovery", t("login.modes.recovery")],
            ] as Array<[AuthMode, string]>).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`seg-link${mode === value ? " active" : ""}`}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>

          {message ? (
            <div className={`inline-message inline-message--${message.tone}`}>{message.body}</div>
          ) : null}

          <form className="auth-form-grid" onSubmit={submit}>
            {mode === "register" ? (
              <label className="auth-field">
                <span>{t("login.fields.name")}</span>
                <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t("login.placeholders.name")} required />
              </label>
            ) : null}
            <label className="auth-field">
              <span>{t("login.fields.email")}</span>
              <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t("login.placeholders.email")} required />
            </label>
            {mode !== "recovery" ? (
              <label className="auth-field">
                <span>{t("login.fields.password")}</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={mode === "register" ? t("login.placeholders.passwordRegister") : t("login.placeholders.password")}
                  required
                />
              </label>
            ) : null}

            <div className="auth-actions">
              <button className="primary-button" type="submit" disabled={busy}>
                {busy ? t("login.working") : copy.action}
              </button>
              {mode !== "register" ? (
                <button className="ghost-button" type="button" onClick={resend} disabled={busy}>
                  {t("login.resend")}
                </button>
              ) : null}
            </div>
          </form>
        </Panel>

        <Panel title={t("login.coverage.title")} kicker={t("login.coverage.kicker")}>
          <div className="auth-checklist">
            <div className="auth-checklist__item">
              <strong>{t("login.coverage.creatorLoginTitle")}</strong>
              <p>{t("login.coverage.creatorLoginBody")}</p>
            </div>
            <div className="auth-checklist__item">
              <strong>{t("login.coverage.registrationTitle")}</strong>
              <p>{t("login.coverage.registrationBody")}</p>
            </div>
            <div className="auth-checklist__item">
              <strong>{t("login.coverage.studentBridgeTitle")}</strong>
              <p>{t("login.coverage.studentBridgeBody")}</p>
            </div>
          </div>
          {status === "authenticated" && user ? (
            <EmptyState
              title={t("login.active.title")}
              body={t("login.active.body")}
              action={
                <div className="auth-actions">
                  <button className="primary-button" type="button" onClick={() => navigate("/account")}>
                    {t("login.active.openAccount")}
                  </button>
                  <button className="ghost-button" type="button" onClick={() => navigate("/")}>
                    {t("login.active.backToUpload")}
                  </button>
                </div>
              }
            />
          ) : null}
        </Panel>
      </div>
    </AppShell>
  );
}
