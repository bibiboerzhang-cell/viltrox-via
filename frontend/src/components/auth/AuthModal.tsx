import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth, type AuthModalMode } from "../../hooks/useAuth";
import { forgotPassword, register, resendVerification } from "../../services/auth.service";

const AUTH_QUERY_MODES = new Set<AuthModalMode>(["signin", "register", "recovery"]);

type AuthOpenEventDetail = {
  mode?: AuthModalMode;
  studentId?: string;
  email?: string;
  qrId?: string;
  key?: string;
};

export function AuthModal() {
  const { t } = useTranslation();
  const {
    status,
    isAuthModalOpen,
    authModalMode,
    signIn,
    acceptSession,
    openAuthModal,
    closeAuthModal,
  } = useAuth();
  const [message, setMessage] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [studentId, setStudentId] = useState("");
  const [studentIdLocked, setStudentIdLocked] = useState(false);
  const [queryAuthIntent, setQueryAuthIntent] = useState(false);
  const appliedAuthQueryRef = useRef("");

  const copy = useMemo(() => {
    if (authModalMode === "register") {
      return {
        title: t("login.copy.register.title"),
        subtitle: t("login.copy.register.subtitle"),
        action: t("login.copy.register.action"),
      };
    }
    if (authModalMode === "recovery") {
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
  }, [authModalMode, t]);

  useEffect(() => {
    if (!isAuthModalOpen) {
      setBusy(false);
      setMessage(null);
      setPassword("");
      setName("");
      setStudentId("");
      setStudentIdLocked(false);
      setQueryAuthIntent(false);
    }
  }, [isAuthModalOpen]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    function openFromDetail(detail: AuthOpenEventDetail) {
      const mode = detail.mode ?? "register";
      if (!AUTH_QUERY_MODES.has(mode)) {
        return;
      }
      const queryKey = detail.key ?? `${mode}:${detail.qrId ?? ""}:${detail.studentId ?? ""}:${detail.email ?? ""}`;
      if (appliedAuthQueryRef.current === queryKey) {
        return;
      }
      appliedAuthQueryRef.current = queryKey;
      const nextStudentId = String(detail.studentId ?? "").trim().toUpperCase();
      const nextEmail = String(detail.email ?? "").trim().toLowerCase();
      if (nextStudentId) {
        setStudentId(nextStudentId);
        setStudentIdLocked(Boolean(detail.qrId));
      }
      if (nextEmail) {
        setEmail(nextEmail);
      }
      setQueryAuthIntent(true);
      openAuthModal(mode);
    }

    function openFromLocation() {
      const queryKey = `${window.location.pathname}${window.location.search}`;
      const params = new URLSearchParams(window.location.search);
      const mode = String(params.get("auth") || params.get("mode") || "").trim().toLowerCase() as AuthModalMode;
      if (!AUTH_QUERY_MODES.has(mode)) {
        return;
      }
      openFromDetail({
        mode,
        studentId: params.get("student_id") || params.get("vid") || "",
        email: params.get("email") || "",
        qrId: params.get("qr_id") || "",
        key: queryKey,
      });
    }

    function handleAuthOpen(event: Event) {
      openFromDetail((event as CustomEvent<AuthOpenEventDetail>).detail ?? {});
    }

    openFromLocation();
    window.addEventListener("viltrox:open-auth", handleAuthOpen);
    window.addEventListener("popstate", openFromLocation);
    return () => {
      window.removeEventListener("viltrox:open-auth", handleAuthOpen);
      window.removeEventListener("popstate", openFromLocation);
    };
  }, [openAuthModal]);

  useEffect(() => {
    if (status === "authenticated" && isAuthModalOpen && !queryAuthIntent) {
      closeAuthModal();
    }
  }, [closeAuthModal, isAuthModalOpen, queryAuthIntent, status]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      if (authModalMode === "signin") {
        await signIn(email, password);
        closeAuthModal();
        return;
      }
      if (authModalMode === "register") {
        const response = await register(name, email, password, studentId);
        if (response.status === "success" && response.token && response.user) {
          acceptSession(response.token, response.user);
          closeAuthModal();
          return;
        }
        setMessage({ tone: "success", body: response.message ?? t("login.messages.registerSuccess") });
        openAuthModal("signin");
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

  if (!isAuthModalOpen) {
    return null;
  }

  return (
    <div className="auth-modal-shell" role="dialog" aria-modal="true" aria-label={copy.title}>
      <button type="button" className="auth-modal-backdrop" aria-label={t("viewer.close")} onClick={closeAuthModal} />
      <div className="auth-modal-card">
        <div className="auth-modal-card__head">
          <div>
            <small>{t("login.eyebrow")}</small>
            <h2>{copy.title}</h2>
            <p>{copy.subtitle}</p>
          </div>
          <button type="button" className="auth-modal-card__close" onClick={closeAuthModal}>
            ×
          </button>
        </div>

        <div className="auth-modal-tabs">
          {([
            ["signin", t("login.modes.signin")],
            ["register", t("login.modes.register")],
            ["recovery", t("login.modes.recovery")],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={authModalMode === value ? "is-active" : ""}
              onClick={() => {
                openAuthModal(value);
                setMessage(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {message ? <div className={`inline-message inline-message--${message.tone}`}>{message.body}</div> : null}

        <form className="auth-modal-form" onSubmit={submit}>
          {authModalMode === "register" ? (
            <label className="auth-field">
              <span>{t("login.fields.name")}</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t("login.placeholders.name")} required />
            </label>
          ) : null}

          {authModalMode === "register" ? (
            <label className="auth-field">
              <span>{t("login.fields.studentId")}</span>
              <input
                value={studentId}
                onChange={(event) => setStudentId(event.target.value)}
                placeholder={studentId.toUpperCase().startsWith("V-SCAD-") ? "V-SCAD-0024" : t("login.placeholders.studentId")}
                readOnly={studentIdLocked}
              />
              <small>{t("login.studentIdHint")}</small>
            </label>
          ) : null}

          <label className="auth-field">
            <span>{t("login.fields.email")}</span>
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t("login.placeholders.email")} required />
          </label>

          {authModalMode !== "recovery" ? (
            <label className="auth-field">
              <span>{t("login.fields.password")}</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={authModalMode === "register" ? t("login.placeholders.passwordRegister") : t("login.placeholders.password")}
                required
              />
            </label>
          ) : null}

          <div className="auth-modal-actions">
            <button className="bw-primary-btn" type="submit" disabled={busy}>
              {busy ? t("login.working") : copy.action}
            </button>
            {authModalMode !== "register" ? (
              <button className="bw-secondary-btn" type="button" onClick={resend} disabled={busy}>
                {t("login.resend")}
              </button>
            ) : null}
          </div>
        </form>
      </div>
    </div>
  );
}
