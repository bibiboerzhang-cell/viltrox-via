import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { LegacyVideoViewer } from "../LegacyVideoViewer";
import {
  apiFetch,
  jsonBody,
  type AuthUser,
  type CreatorAddress,
  type CreatorProgramResponse,
  type CreatorSocialAccount,
  type CreatorSubmission,
  type RedemptionRecord,
} from "../../lib/api";
import {
  buildSubmissionViewerData,
  submissionCleanliness,
  submissionDateLabel,
  submissionGear,
  submissionQuality,
  submissionSpeed,
  submissionStatusLabel,
    submissionStatusTone,
    type LegacyVideoViewerData,
  } from "../../lib/legacyVideo";
import type { StudentPassResponse, VerificationRecord } from "../../types/api";
import {
  addCreatorSocialAccount,
  createCreatorAddress,
  deleteCreatorSocialAccount,
  setDefaultAddress,
} from "../../services/creator.service";
import { changePassword } from "../../services/auth.service";
import { fetchStudentPass } from "../../services/student.service";
import { listMyVerifications, markVerificationPosted, startVerification } from "../../services/verify.service";

export type AccountTab = "platforms" | "submissions" | "orders" | "settings";

function initials(name: string) {
  return (name || "Creator")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || "")
    .join("") || "C";
}

function platformLabel(platform: string) {
  return platform.charAt(0).toUpperCase() + platform.slice(1);
}

function normalizePlatform(platform: string) {
  return platform.trim().toLowerCase();
}

async function copyText(value: string) {
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement("textarea");
  input.value = value;
  document.body.appendChild(input);
  input.select();
  document.execCommand("copy");
  document.body.removeChild(input);
}

function statusClassName(account?: CreatorSocialAccount) {
  if (!account) return "muted";
  if (account.verified) return "success";
  if (account.verify_code) return "pending";
  return "muted";
}

function statusLabel(
  account: CreatorSocialAccount | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  if (!account) return t("account.hub.status.notConnected");
  if (account.verified) return t("account.hub.status.approved");
  if (account.verify_code) return t("account.hub.status.pending");
  return t("account.hub.status.notConnected");
}

function platformDescription(
  account: CreatorSocialAccount | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
) {
  if (!account) return t("account.hub.platform.notConnected");
  if (account.verified) return t("account.hub.platform.verified", { handle: account.handle });
  if (account.verify_code) return t("account.hub.platform.pending", { handle: account.handle });
  return account.handle || t("account.hub.platform.notConnected");
}

function latestVerificationForPlatform(verifications: VerificationRecord[], platform: string) {
  return verifications.find((item) => normalizePlatform(item.platform || "") === platform);
}

export function AccountHub({
  user,
  token,
  addresses,
  socialAccounts,
  submissions,
  redemptions,
  program,
  onSaved,
  onSignOut,
  onClose,
  initialTab = "platforms",
}: {
  user: AuthUser;
  token: string;
  addresses: CreatorAddress[];
  socialAccounts: CreatorSocialAccount[];
  submissions: CreatorSubmission[];
  redemptions: RedemptionRecord[];
  program: CreatorProgramResponse | null;
  onSaved: () => void | Promise<void>;
  onSignOut: () => void;
  onClose: () => void;
  initialTab?: AccountTab;
}) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<AccountTab>(initialTab);
  const [name, setName] = useState(user.name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(user.avatar_url ?? "");
  const [bio, setBio] = useState(user.bio ?? "");
  const [saving, setSaving] = useState(false);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [viewerData, setViewerData] = useState<LegacyVideoViewerData | null>(null);
  const [socialRows, setSocialRows] = useState<CreatorSocialAccount[]>(socialAccounts);
  const [verifications, setVerifications] = useState<VerificationRecord[]>([]);
  const [studentPass, setStudentPass] = useState<StudentPassResponse | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [socialDraft, setSocialDraft] = useState({ platform: "instagram", handle: "" });
  const [addressDraft, setAddressDraft] = useState({
    name: user.name ?? "",
    phone: "",
    address1: "",
    address2: "",
    city: "",
    state: "",
    country: "US",
    postal_code: "",
  });
  const [passwordDraft, setPasswordDraft] = useState({
    currentPassword: "",
    newPassword: "",
  });

  useEffect(() => {
    setName(user.name ?? "");
    setAvatarUrl(user.avatar_url ?? "");
    setBio(user.bio ?? "");
  }, [user]);

  useEffect(() => {
    setActiveTab(initialTab);
  }, [initialTab]);

  useEffect(() => {
    setSocialRows(socialAccounts);
  }, [socialAccounts]);

  useEffect(() => {
    let active = true;
    const shouldLoadStudentPass = Boolean(program?.student?.is_active || program?.student?.student_id_code);
    async function hydrateRuntimePanels() {
      try {
        const [verificationRows, pass] = await Promise.all([
          listMyVerifications(token).catch(() => []),
          shouldLoadStudentPass ? fetchStudentPass(token).catch(() => null) : Promise.resolve(null),
        ]);
        if (!active) {
          return;
        }
        setVerifications(verificationRows);
        setStudentPass(pass && pass.status === "success" ? pass : null);
      } catch {
        if (active) {
          setVerifications([]);
          setStudentPass(null);
        }
      }
    }
    void hydrateRuntimePanels();
    return () => {
      active = false;
    };
  }, [program?.student?.is_active, program?.student?.student_id_code, token]);

  const vip = program?.vip;
  const affiliate = program?.affiliate;
  const trust = program?.trust;

  const platformRows = useMemo(() => {
    const rows = ["tiktok", "instagram", "facebook", "youtube", "reddit"].map((platform) => {
      const found = socialRows.find((item) => normalizePlatform(item.platform) === platform);
      return { platform, account: found, verification: latestVerificationForPlatform(verifications, platform) };
    });
    return rows;
  }, [socialRows, verifications]);

  async function saveProfile() {
    setSaving(true);
    try {
      await apiFetch(
        "/api/creator/profile",
        {
          method: "PATCH",
          body: jsonBody({ name, avatar_url: avatarUrl, bio }),
        },
        token,
      );
      await onSaved();
      setFlash({ tone: "success", body: t("account.hub.flash.profileSaved") });
    } finally {
      setSaving(false);
    }
  }

  async function savePassword() {
    if (!passwordDraft.currentPassword.trim() || !passwordDraft.newPassword.trim()) {
      setFlash({ tone: "warning", body: t("account.hub.flash.passwordMissing") });
      return;
    }
    if (passwordDraft.newPassword.length < 8) {
      setFlash({ tone: "warning", body: t("account.hub.flash.passwordTooShort") });
      return;
    }
    setPasswordSaving(true);
    setFlash(null);
    try {
      const response = await changePassword(token, passwordDraft.currentPassword, passwordDraft.newPassword);
      if (response.status !== "success") {
        throw new Error(response.message || t("account.hub.flash.passwordUpdateError"));
      }
      setPasswordDraft({ currentPassword: "", newPassword: "" });
      setFlash({ tone: "success", body: response.message || t("account.hub.flash.passwordUpdated") });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.passwordUpdateError"),
      });
    } finally {
      setPasswordSaving(false);
    }
  }

  async function connectPlatform(prefillPlatform?: string) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await addCreatorSocialAccount(token, {
        platform: prefillPlatform || socialDraft.platform,
        handle: socialDraft.handle.trim(),
      });
      if (response.status !== "success") {
        throw new Error(response.message || t("account.hub.flash.connectError"));
      }
      const latest = await apiFetch<{ accounts: CreatorSocialAccount[] }>("/api/creator/social-accounts", {}, token);
      setSocialRows(latest.accounts || []);
      setFlash({
        tone: "success",
        body: t("account.hub.flash.connectSuccess", {
          platform: platformLabel(response.platform || socialDraft.platform),
          codeLabel: response.verify_code || t("account.hub.verificationCodeLower"),
        }),
      });
      setSocialDraft((current) => ({ ...current, handle: "" }));
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.connectError") });
    } finally {
      setSaving(false);
    }
  }

  async function removePlatform(accountId: number) {
    setSaving(true);
    setFlash(null);
    try {
      await deleteCreatorSocialAccount(token, accountId);
      setSocialRows((current) => current.filter((item) => item.id !== accountId));
      setVerifications(await listMyVerifications(token).catch(() => verifications));
      setFlash({ tone: "warning", body: t("account.hub.flash.platformRemoved") });
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.removeError") });
    } finally {
      setSaving(false);
    }
  }

  async function beginVerification(platform: string, handle: string) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await startVerification(token, { platform, handle });
      const next = await listMyVerifications(token);
      setVerifications(next);
      setFlash({
        tone: "success",
        body: t("account.hub.flash.verificationStarted", { platform: platformLabel(platform) }),
      });
      await copyText(response.generated_comment || response.code || "");
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.verifyStartError") });
    } finally {
      setSaving(false);
    }
  }

  async function postedVerification(verificationId: number) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await markVerificationPosted(token, verificationId);
      const next = await listMyVerifications(token);
      setVerifications(next);
      setFlash({
        tone: "success",
        body: response.message || t("account.hub.flash.markPostedSuccess"),
      });
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.markPostedError") });
    } finally {
      setSaving(false);
    }
  }

  async function addAddress() {
    setSaving(true);
    setFlash(null);
    try {
      const response = await createCreatorAddress(token, addressDraft);
      if (response.status !== "success") {
        throw new Error(response.message || t("account.hub.flash.addressSaveError"));
      }
      setFlash({ tone: "success", body: t("account.hub.flash.addressSaved") });
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.addressSaveError") });
    } finally {
      setSaving(false);
    }
  }

  async function makeDefaultAddress(addressId: number) {
    setSaving(true);
    setFlash(null);
    try {
      await setDefaultAddress(token, addressId);
      setFlash({ tone: "success", body: t("account.hub.flash.defaultAddressUpdated") });
    } catch (error) {
      setFlash({ tone: "danger", body: error instanceof Error ? error.message : t("account.hub.flash.defaultAddressError") });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="sheet account-sheet">
      <div className="sheet-top">
        <div>
          <h3>{t("account.hub.title")}</h3>
          <p>{t("account.hub.subtitle")}</p>
        </div>
        <button className="close" type="button" onClick={onClose}>
          ×
        </button>
      </div>

      <div className="auth-tabs">
        <button className="active" type="button">
          {t("account.hub.myAccount")}
        </button>
      </div>

      <div className="profile-header">
        <div className="profile-card">
          <div className="profile-avatar">{initials(name || user.name || "Creator")}</div>
          <div className="profile-meta">
            <h4>{name || user.name || t("account.hub.creatorFallback")}</h4>
            <p>{user.email}</p>
            <div className="creator-meta-line">
              <p id="profileCreatorCode">{t("account.hub.creatorId")}: {user.creator_code || "—"}</p>
              <button className="creator-copy-btn" type="button" onClick={() => void copyText(user.creator_code || "")}>
                {t("account.hub.copyId")}
              </button>
              <span className="creator-pill">{vip?.badge_text || t("account.hub.vipFallback", { tier: vip?.tier_label || "—" })}</span>
            </div>
          </div>
        </div>
        <div className="account-actions">
          <button className="danger-btn" type="button" onClick={onSignOut}>
            {t("account.hub.signOut")}
          </button>
        </div>
      </div>

      <div className="profile-stats">
        <button className="stat-box" type="button" onClick={() => setActiveTab("submissions")}>
          <small>{t("account.hub.mySubmissions")}</small>
          <strong>{submissions.length}</strong>
        </button>
        <button className="stat-box" type="button" onClick={() => setActiveTab("platforms")}>
          <small>{t("account.hub.availablePoints")}</small>
          <strong>{Number(user.points_balance || 0).toLocaleString()}</strong>
        </button>
        <button className="stat-box" type="button" onClick={() => setActiveTab("settings")}>
          <small>{t("account.hub.vipStatus")}</small>
          <strong>{vip?.tier_label || t("account.hub.pending")}</strong>
        </button>
      </div>

      {flash ? <div className={`inline-message inline-message--${flash.tone}`}>{flash.body}</div> : null}

      <div className="program-grid">
        <section className="program-card">
          <small>{t("account.hub.vipStatus")}</small>
          <div className="program-card__head">
            <h5>{vip?.tier_label || t("account.hub.pending")}</h5>
            <span className={`vip-badge ${vip?.is_active ? "active" : "pending"}`}>
              {vip?.badge_text || t("account.hub.awaitingVideo")}
            </span>
          </div>
          <div className="program-meta emphasis">
            {vip?.is_active
              ? t("account.hub.vipActiveMeta", {
                  multiplier: (vip.points_multiplier || 1).toFixed(1),
                  commission: Math.round((vip.commission_rate || 0) * 100),
                })
              : vip?.activation_message || t("account.hub.vipInactiveMeta")}
          </div>
          <div className="program-progress">
            <div style={{ width: `${Math.round((vip?.progress_ratio || 0) * 100)}%` }} />
          </div>
          <div className="program-meta">
            {vip?.next_tier_label
              ? t("account.hub.nextTierMeta", {
                  points: vip?.next_threshold_points || 0,
                  videos: vip?.next_threshold_videos || 0,
                  tier: vip.next_tier_label,
                })
              : t("account.hub.topTierUnlocked")}
          </div>
          <div className="program-meta">
            {t("account.hub.vipProgressMeta", {
              confirmedVideos: vip?.confirmed_videos || 0,
              targetVideos: vip?.next_threshold_videos || vip?.threshold_videos || 1,
              currentPoints: vip?.current_points || 0,
              videoLane: Math.round((vip?.video_progress_ratio || 0) * 100),
              pointLane: Math.round((vip?.points_progress_ratio || 0) * 100),
            })}
          </div>
          <div className="program-meta">
            <span className="trust-pill">
              {t("account.hub.trustPill", { score: Math.round(Number(trust?.score || 0)), label: trust?.label || t("account.hub.normal") })}
            </span>
          </div>
        </section>

        <section className="program-card">
          <small>{t("account.hub.affiliateLink")}</small>
          <h5>{t("account.hub.trackedOrders", { count: Number(affiliate?.orders_count || 0).toLocaleString() })}</h5>
          <div className="affiliate-link-row">
            <input
              className="affiliate-link-input"
              readOnly
              value={affiliate?.affiliate_link || affiliate?.preview_link || ""}
              placeholder={t("account.hub.affiliatePlaceholder")}
            />
            <button
              className="affiliate-copy-btn"
              type="button"
              disabled={!(affiliate?.affiliate_link || affiliate?.preview_link)}
              onClick={() => void copyText(affiliate?.affiliate_link || affiliate?.preview_link || "")}
            >
              {t("account.hub.copy")}
            </button>
          </div>
          <div className="program-meta">
            {affiliate?.activation_message ||
              t("account.hub.affiliateMeta")}
          </div>
        </section>
      </div>

      {studentPass?.pass_url ? (
        <section className="student-pass-card">
          <div>
            <small>{t("account.hub.studentPass")}</small>
            <h5>{studentPass.student_id_code || t("account.hub.studentPassFallback")}</h5>
            <p>{t("account.hub.studentPassBody")}</p>
          </div>
          <div className="student-pass-card__media">
            {studentPass.qr_data_uri ? <img src={studentPass.qr_data_uri} alt="Student pass QR" /> : null}
            <div className="student-pass-card__actions">
              <button className="outline-btn" type="button" onClick={() => void copyText(studentPass.pass_url || "")}>
                {t("account.hub.copyPassUrl")}
              </button>
              <span>{studentPass.expires_at || t("account.hub.rotatesEachMinute")}</span>
            </div>
          </div>
        </section>
      ) : null}

      <div className="acct-tabs">
        {[
          ["platforms", t("account.hub.tabs.platforms")],
          ["submissions", t("account.hub.tabs.submissions")],
          ["orders", t("account.hub.tabs.orders")],
          ["settings", t("account.hub.tabs.settings")],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`acct-tab${activeTab === key ? " active" : ""}`}
            onClick={() => setActiveTab(key as AccountTab)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className={`acct-pane${activeTab === "platforms" ? " active" : ""}`}>
        <div className="account-inline-form">
          <div className="account-inline-form__head">
            <strong>{t("account.hub.connectPlatformTitle")}</strong>
            <span>{t("account.hub.connectPlatformBody")}</span>
          </div>
          <div className="account-inline-form__fields">
            <select
              className="input"
              value={socialDraft.platform}
              onChange={(event) => setSocialDraft({ ...socialDraft, platform: event.target.value })}
            >
              {["instagram", "tiktok", "youtube", "facebook", "reddit"].map((platform) => (
                <option key={platform} value={platform}>
                  {platformLabel(platform)}
                </option>
              ))}
            </select>
            <input
              className="input"
              value={socialDraft.handle}
              onChange={(event) => setSocialDraft({ ...socialDraft, handle: event.target.value })}
              placeholder={t("account.hub.handlePlaceholder")}
            />
            <button className="black-btn" type="button" disabled={saving || !socialDraft.handle.trim()} onClick={() => void connectPlatform()}>
              {saving ? t("account.hub.working") : t("account.hub.linkAccount")}
            </button>
          </div>
        </div>
        <div className="platform-list">
          {platformRows.map(({ platform, account, verification }) => (
            <div key={platform} className="platform-row">
              <div className="platform-left">
                <div className="platform-head">
                  <strong>{platformLabel(platform)}</strong>
                  <span className={`pill ${statusClassName(account)}`}>{statusLabel(account, t)}</span>
                </div>
                <span>{platformDescription(account, t)}</span>
                {account?.verify_code ? <span className="platform-note">{t("account.hub.verifyCode")}: {account.verify_code}</span> : null}
                {verification ? (
                  <span className="platform-note">
                    {t("account.hub.verification")}: {verification.status}
                    {verification.expires_at ? ` · ${t("account.hub.expires")} ${verification.expires_at}` : ""}
                  </span>
                ) : null}
              </div>
              <div className="platform-actions">
                {account ? (
                  <>
                    <button className={`outline-btn ${statusClassName(account)}`} type="button" onClick={() => void copyText(account.verify_code || "")}>
                      {t("account.hub.copyCode")}
                    </button>
                    {!account.verified ? (
                      <button className="outline-btn success" type="button" onClick={() => void beginVerification(platform, account.handle)}>
                        {t("account.hub.startVerify")}
                      </button>
                    ) : null}
                    {verification && verification.status === "pending" ? (
                      <button className="outline-btn warning" type="button" onClick={() => void postedVerification(verification.id)}>
                        {t("account.hub.markPosted")}
                      </button>
                    ) : null}
                    <button className="outline-btn muted" type="button" onClick={() => void removePlatform(account.id)}>
                      {t("account.hub.remove")}
                    </button>
                  </>
                ) : (
                  <button
                    className="outline-btn"
                    type="button"
                    onClick={() => {
                      setSocialDraft((current) => ({ ...current, platform }));
                    }}
                  >
                    {t("account.hub.useThisSlot")}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
        {verifications.length ? (
          <div className="verification-stack">
            {verifications.slice(0, 4).map((item) => (
              <article key={item.id} className="verification-card">
                <div>
                  <strong>{platformLabel(item.platform)} · @{item.handle}</strong>
                  <p>{item.generated_comment || item.note || t("account.hub.verificationCreated")}</p>
                </div>
                <div className="verification-card__meta">
                  <span>{item.status}</span>
                  {item.comment_job_id ? <span>{t("account.hub.jobLabel", { id: item.comment_job_id })}</span> : null}
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </div>

      <div className={`acct-pane${activeTab === "settings" ? " active" : ""}`}>
        <div className="mini-fields">
          <div className="mini-field">
            <label>{t("account.hub.profile.avatarUrl")}</label>
            <input className="input" value={avatarUrl} onChange={(event) => setAvatarUrl(event.target.value)} placeholder={t("account.hub.profile.avatarPlaceholder")} />
          </div>
          <div className="mini-field">
            <label>{t("account.hub.profile.displayName")}</label>
            <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder={t("account.hub.profile.namePlaceholder")} />
          </div>
          <div className="mini-field">
            <label>{t("account.hub.profile.bio")}</label>
            <textarea className="input" value={bio} onChange={(event) => setBio(event.target.value)} placeholder={t("account.hub.profile.bioPlaceholder")} rows={2} />
          </div>
        </div>
        <button className="black-btn account-save-btn" type="button" onClick={saveProfile} disabled={saving}>
          {saving ? t("account.hub.saving") : t("account.hub.saveProfile")}
        </button>
        <div className="account-inline-form account-inline-form--stack">
          <div className="account-inline-form__head">
            <strong>{t("account.hub.password.title")}</strong>
            <span>{t("account.hub.password.body")}</span>
          </div>
          <div className="mini-fields">
            <div className="mini-field">
              <label>{t("account.hub.password.currentLabel")}</label>
              <input
                className="input"
                type="password"
                autoComplete="current-password"
                value={passwordDraft.currentPassword}
                onChange={(event) => setPasswordDraft({ ...passwordDraft, currentPassword: event.target.value })}
                placeholder={t("account.hub.password.currentPlaceholder")}
              />
            </div>
            <div className="mini-field">
              <label>{t("account.hub.password.newLabel")}</label>
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={passwordDraft.newPassword}
                onChange={(event) => setPasswordDraft({ ...passwordDraft, newPassword: event.target.value })}
                placeholder={t("account.hub.password.newPlaceholder")}
              />
            </div>
          </div>
          <button className="outline-btn account-full-btn" type="button" onClick={() => void savePassword()} disabled={passwordSaving}>
            {passwordSaving ? t("account.hub.saving") : t("account.hub.password.action")}
          </button>
        </div>

        <div className="account-inline-form account-inline-form--stack">
          <div className="account-inline-form__head">
            <strong>{t("account.hub.addShippingAddress")}</strong>
            <span>{t("account.hub.addShippingAddressBody")}</span>
          </div>

          <div className="platform-list">
          {addresses.length ? (
            addresses.map((address) => (
              <div key={address.id} className="platform-row">
                <div className="platform-left">
                  <div className="platform-head">
                    <strong>{address.name || t("account.hub.primaryRecipient")}</strong>
                    {address.is_default ? <span className="pill success">{t("account.hub.default")}</span> : null}
                  </div>
                  <span>
                    {[address.address1, address.city, address.state, address.postal_code, address.country]
                      .filter(Boolean)
                      .join(", ")}
                  </span>
                </div>
                <div className="platform-actions">
                  <button className="outline-btn muted" type="button" onClick={() => void makeDefaultAddress(address.id)}>
                    {t("account.hub.makeDefault")}
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="legacy-empty-row">{t("account.hub.noAddresses")}</div>
          )}
          </div>
          <div className="account-address-grid">
            <input className="input" value={addressDraft.name} onChange={(event) => setAddressDraft({ ...addressDraft, name: event.target.value })} placeholder={t("account.hub.address.recipient")} />
            <input className="input" value={addressDraft.phone} onChange={(event) => setAddressDraft({ ...addressDraft, phone: event.target.value })} placeholder={t("account.hub.address.phone")} />
            <input className="input account-address-grid__full" value={addressDraft.address1} onChange={(event) => setAddressDraft({ ...addressDraft, address1: event.target.value })} placeholder={t("account.hub.address.line1")} />
            <input className="input account-address-grid__full" value={addressDraft.address2} onChange={(event) => setAddressDraft({ ...addressDraft, address2: event.target.value })} placeholder={t("account.hub.address.line2")} />
            <input className="input" value={addressDraft.city} onChange={(event) => setAddressDraft({ ...addressDraft, city: event.target.value })} placeholder={t("account.hub.address.city")} />
            <input className="input" value={addressDraft.state} onChange={(event) => setAddressDraft({ ...addressDraft, state: event.target.value })} placeholder={t("account.hub.address.state")} />
            <input className="input" value={addressDraft.postal_code} onChange={(event) => setAddressDraft({ ...addressDraft, postal_code: event.target.value })} placeholder={t("account.hub.address.postalCode")} />
            <input className="input" value={addressDraft.country} onChange={(event) => setAddressDraft({ ...addressDraft, country: event.target.value })} placeholder={t("account.hub.address.country")} />
          </div>
          <button className="outline-btn account-full-btn" type="button" onClick={() => void addAddress()} disabled={saving || !addressDraft.address1 || !addressDraft.city}>
            {saving ? t("account.hub.saving") : t("account.hub.addNewAddress")}
          </button>
        </div>
      </div>

      <div className={`acct-pane${activeTab === "submissions" ? " active" : ""}`}>
        <div className="platform-list profile-submission-list">
          {submissions.length ? (
            submissions.map((submission) => (
              <div key={submission.id} className="profile-submission-card">
                <div className="profile-submission-main">
                  <div className="profile-submission-title">{submission.title || `Submission #${submission.id}`}</div>
                  <div className="profile-submission-sub">
                    {submission.platform || t("account.hub.unknown")} · {submissionDateLabel(submission.created_at)} · {Number(submission.points_awarded || 0).toLocaleString()} {t("account.hub.pts")}
                  </div>
                  <div className="profile-submission-metrics">
                    <span className="profile-submission-metric">{t("account.hub.metrics.clean")} {submissionCleanliness(submission) ?? "—"}</span>
                    <span className="profile-submission-metric">{t("account.hub.metrics.speed")} {submissionSpeed(submission) ?? "—"}</span>
                    <span className="profile-submission-metric">{t("account.hub.metrics.quality")} {submissionQuality(submission) ?? "—"}</span>
                    <span className="profile-submission-metric">{submissionGear(submission)}</span>
                  </div>
                </div>
                <div className="profile-submission-right">
                  <div className={`profile-submission-status ${submissionStatusTone(submission.detection_status)}`}>
                    {submissionStatusLabel(submission.detection_status)}
                  </div>
                  <button
                    className="profile-submission-view-btn"
                    type="button"
                    onClick={() => setViewerData(buildSubmissionViewerData(submission, user.creator_code || ""))}
                  >
                    {t("account.hub.view")}
                  </button>
                </div>
              </div>
            ))
          ) : (
            <div className="legacy-empty-row">{t("account.hub.noSubmissions")}</div>
          )}
        </div>
      </div>

      <div className={`acct-pane${activeTab === "orders" ? " active" : ""}`}>
        <div className="platform-list">
          {redemptions.length ? (
            redemptions.map((record) => (
              <div key={record.id} className="platform-row">
                <div className="platform-left">
                  <div className="platform-head">
                    <strong>{record.item_name || `Reward #${record.id}`}</strong>
                    <span className="pill muted">{record.status || t("account.hub.pending")}</span>
                  </div>
                  <span>
                    {record.created_at || t("account.hub.pendingDate")} · -{Number(record.points_cost || 0).toLocaleString()} {t("account.hub.pts")}
                  </span>
                </div>
              </div>
            ))
          ) : (
            <div className="legacy-empty-row">{t("account.hub.noOrders")}</div>
          )}
        </div>
      </div>

      <LegacyVideoViewer open={Boolean(viewerData)} data={viewerData} onClose={() => setViewerData(null)} />
    </div>
  );
}
