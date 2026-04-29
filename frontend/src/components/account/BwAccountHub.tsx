import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AppleWalletButton } from "../AppleWalletButton";
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
  deleteCreatorAddress,
  deleteCreatorSocialAccount,
  listCreatorAddresses,
  listCreatorSocialAccounts,
  setDefaultAddress,
} from "../../services/creator.service";
import { changePassword } from "../../services/auth.service";
import { fetchStudentPass } from "../../services/student.service";
import { listMyVerifications, markVerificationPosted, startVerification } from "../../services/verify.service";

export type AccountTab = "platforms" | "submissions" | "orders" | "profile" | "vip" | "settings";

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

function platformAccent(platform: string) {
  switch (normalizePlatform(platform)) {
    case "youtube":
      return "#f87171";
    case "tiktok":
      return "#f472b6";
    case "instagram":
      return "#c084fc";
    case "facebook":
      return "#60a5fa";
    case "reddit":
      return "#fb923c";
    default:
      return "rgba(255,255,255,0.7)";
  }
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
  if (!account) return "unverified";
  if (account.verified) return "verified";
  if (account.verify_code) return "pending";
  return "unverified";
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

function scoreClass(score: number) {
  if (score >= 300) return "elite";
  if (score >= 200) return "high";
  return "mid";
}

function createAddressDraft(user: AuthUser) {
  return {
    name: user.name ?? "",
    phone: "",
    address1: "",
    address2: "",
    city: "",
    state: "",
    country: "US",
    postal_code: "",
  };
}

function formatDateTimeLabel(value: string, locale: string) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return date.toLocaleString(locale === "zh" ? "zh-CN" : "en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function tabIcon(tab: AccountTab) {
  if (tab === "platforms") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
        <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
      </svg>
    );
  }
  if (tab === "submissions") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
      </svg>
    );
  }
  if (tab === "orders") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M16 16H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h10l2 3v8a1 1 0 0 1-1 1z" />
        <path d="M12 17v4m-4-4h8" />
      </svg>
    );
  }
  if (tab === "profile") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M20 21a8 8 0 0 0-16 0" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    );
  }
  if (tab === "vip") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M6 20h12" />
        <path d="M6 20 4 7l5 4 3-6 3 6 5-4-2 13" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export function BwAccountHub({
  user,
  token,
  addresses,
  socialAccounts,
  submissions,
  redemptions,
  program,
  onSaved,
  onSignOut,
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
  initialTab?: AccountTab;
}) {
  const { t, i18n } = useTranslation();
  const [activeTab, setActiveTab] = useState<AccountTab>(initialTab);
  const [name, setName] = useState(user.name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(user.avatar_url ?? "");
  const [bio, setBio] = useState(user.bio ?? "");
  const [saving, setSaving] = useState(false);
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [viewerData, setViewerData] = useState<LegacyVideoViewerData | null>(null);
  const [socialRows, setSocialRows] = useState<CreatorSocialAccount[]>(socialAccounts);
  const [addressRows, setAddressRows] = useState<CreatorAddress[]>(addresses);
  const [verifications, setVerifications] = useState<VerificationRecord[]>([]);
  const [studentPass, setStudentPass] = useState<StudentPassResponse | null>(null);
  const [flash, setFlash] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [qrWalletMessage, setQrWalletMessage] = useState("");
  const [socialDraft, setSocialDraft] = useState({ platform: "instagram", handle: "" });
  const [addressDraft, setAddressDraft] = useState(() => createAddressDraft(user));
  const [addressComposerOpen, setAddressComposerOpen] = useState(false);
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
    setAddressRows(addresses);
  }, [addresses]);

  useEffect(() => {
    if (!addressComposerOpen) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAddressComposerOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [addressComposerOpen]);

  useEffect(() => {
    if (!qrModalOpen) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setQrModalOpen(false);
        setQrWalletMessage("");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [qrModalOpen]);

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
  const studentProgram = program?.student;
  const identityCard = program?.identity_cards?.student_cards?.find((card) => String(card.public_vid || "").trim());
  const trust = program?.trust;
  const creatorCodeValue = String(user.creator_code || "").trim();
  const creatorCode = creatorCodeValue || "—";
  const publicVid = String(studentProgram?.student_id_code || studentPass?.student_id_code || identityCard?.public_vid || "").trim();
  const publicIdentity = publicVid || creatorCodeValue;
  const encodedPublicVid = publicIdentity ? encodeURIComponent(publicIdentity) : "";
  const publicProfileUrl = encodedPublicVid ? `/vid/${encodedPublicVid}` : "";
  const publicShareCardUrl = encodedPublicVid ? `/api/public/vid/${encodedPublicVid}/share-card.png` : "";
  const publicShareCardDownloadUrl = publicShareCardUrl ? `${publicShareCardUrl}?download=1` : "";
  const pointsBalance = Number(user.points_balance ?? 0);
  const affiliateLink = affiliate?.affiliate_link || affiliate?.preview_link || "";
  const affiliateOrders = Number(affiliate?.orders_count || 0);
  const affiliateRevenue = Number(affiliate?.revenue_total || 0);
  const effectiveCommissionRate = Number(affiliate?.effective_commission_rate ?? vip?.commission_rate ?? 0);
  const estimatedCommission = affiliateRevenue * effectiveCommissionRate;
  const hasStudentLane = Boolean(studentProgram?.is_active || studentPass?.student_id_code || studentPass?.pass_url);
  const moneyFormatter = new Intl.NumberFormat(i18n.language === "zh" ? "zh-CN" : "en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
  const vipUpgradeTiers = [
    { key: "bronze", label: "Bronze", points: 0, videos: 1, commission: 5, multiplier: "1.0x" },
    { key: "silver", label: "Silver", points: 500, videos: 3, commission: 6, multiplier: "1.2x" },
    { key: "gold", label: "Gold", points: 2000, videos: 8, commission: 7, multiplier: "1.5x" },
    { key: "platinum", label: "Platinum", points: 5000, videos: 20, commission: 10, multiplier: "2.0x" },
  ] as const;

  const platformRows = useMemo(() => {
    return ["youtube", "tiktok", "instagram", "facebook", "reddit"].map((platform) => {
      const found = socialRows.find((item) => normalizePlatform(item.platform) === platform);
      return { platform, account: found, verification: latestVerificationForPlatform(verifications, platform) };
    });
  }, [socialRows, verifications]);

  async function refreshAddresses() {
    const nextAddresses = await listCreatorAddresses(token);
    setAddressRows(nextAddresses);
  }

  async function refreshSocials() {
    const nextAccounts = await listCreatorSocialAccounts(token);
    setSocialRows(nextAccounts);
  }

  async function refreshVerifications() {
    const next = await listMyVerifications(token).catch(() => verifications);
    setVerifications(next);
  }

  async function saveProfile() {
    setSaving(true);
    setFlash(null);
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
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.profileSaveError"),
      });
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
    const nextPlatform = prefillPlatform || socialDraft.platform;
    if (!socialDraft.handle.trim()) {
      setFlash({ tone: "warning", body: t("account.hub.connectPlatformBody") });
      return;
    }
    setSaving(true);
    setFlash(null);
    try {
      const response = await addCreatorSocialAccount(token, {
        platform: nextPlatform,
        handle: socialDraft.handle.trim(),
      });
      if (response.status !== "success") {
        throw new Error(response.message || t("account.hub.flash.connectError"));
      }
      await refreshSocials();
      setFlash({
        tone: "success",
        body: t("account.hub.flash.connectSuccess", {
          platform: platformLabel(response.platform || nextPlatform),
          codeLabel: response.verify_code || t("account.hub.verificationCodeLower"),
        }),
      });
      setSocialDraft({ platform: nextPlatform, handle: "" });
      setActiveTab("platforms");
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.connectError"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function removePlatform(accountId: number) {
    setSaving(true);
    setFlash(null);
    try {
      await deleteCreatorSocialAccount(token, accountId);
      await Promise.all([refreshSocials(), refreshVerifications()]);
      setFlash({ tone: "warning", body: t("account.hub.flash.platformRemoved") });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.removeError"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function beginVerification(platform: string, handle: string) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await startVerification(token, { platform, handle });
      await refreshVerifications();
      setFlash({
        tone: "success",
        body: t("account.hub.flash.verificationStarted", { platform: platformLabel(platform) }),
      });
      await copyText(response.generated_comment || response.code || "");
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.verifyStartError"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function postedVerification(verificationId: number) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await markVerificationPosted(token, verificationId);
      await refreshVerifications();
      setFlash({
        tone: "success",
        body: response.message || t("account.hub.flash.markPostedSuccess"),
      });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.markPostedError"),
      });
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
      await refreshAddresses();
      setAddressDraft(createAddressDraft(user));
      setAddressComposerOpen(false);
      setFlash({ tone: "success", body: t("account.hub.flash.addressSaved") });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.addressSaveError"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function makeDefaultAddress(addressId: number) {
    setSaving(true);
    setFlash(null);
    try {
      await setDefaultAddress(token, addressId);
      await refreshAddresses();
      setFlash({ tone: "success", body: t("account.hub.flash.defaultAddressUpdated") });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.defaultAddressError"),
      });
    } finally {
      setSaving(false);
    }
  }

  async function removeAddress(addressId: number) {
    setSaving(true);
    setFlash(null);
    try {
      const response = await deleteCreatorAddress(token, addressId);
      if (response.status !== "deleted" && response.status !== "success") {
        throw new Error(response.message || t("account.hub.flash.addressDeleteError"));
      }
      await refreshAddresses();
      setFlash({ tone: "success", body: t("account.hub.flash.addressDeleted") });
    } catch (error) {
      setFlash({
        tone: "danger",
        body: error instanceof Error ? error.message : t("account.hub.flash.addressDeleteError"),
      });
    } finally {
      setSaving(false);
    }
  }

  function openAddressComposer() {
    setAddressDraft(createAddressDraft(user));
    setAddressComposerOpen(true);
  }

  function closeAddressComposer() {
    setAddressComposerOpen(false);
    setAddressDraft(createAddressDraft(user));
  }

  function openProfileQrModal() {
    setQrWalletMessage("");
    setQrModalOpen(true);
  }

  function closeProfileQrModal() {
    setQrModalOpen(false);
    setQrWalletMessage("");
  }

  function handleWalletClick() {
    setQrWalletMessage("Apple Wallet 需要 Apple Developer Pass 证书和签名文件；接入后这里会直接添加你的 Viltrox 身份卡。");
  }

  return (
    <div className="bw-profile-shell">
      <header className="bw-profile-header">
        <button className="bw-profile-avatar" type="button" onClick={() => setActiveTab("settings")}>
          {avatarUrl ? <img src={avatarUrl} alt={name || user.name || t("account.hub.creatorFallback")} /> : <span>{initials(name || user.name || "Creator")}</span>}
        </button>
        <h1 className="bw-profile-name">{name || user.name || t("account.hub.creatorFallback")}</h1>
        <div className="bw-profile-code-row">
          <button className="bw-profile-code bw-profile-code-button" type="button" onClick={openProfileQrModal} disabled={!publicShareCardUrl}>
            {creatorCode}
          </button>
          <button className="bw-inline-chip" type="button" onClick={() => void copyText(creatorCode)}>
            {t("account.hub.copyId")}
          </button>
        </div>
        {bio ? <p className="bw-profile-bio">{bio}</p> : <p className="bw-profile-email">{user.email}</p>}
        <div className="bw-profile-tier">{vip?.badge_text || t("account.hub.vipFallback", { tier: vip?.tier_label || "—" })}</div>
        <div className="bw-profile-stats">
          <button className="bw-profile-stat" type="button" onClick={() => setActiveTab("submissions")}>
            <strong>{submissions.length}</strong>
            <span>{t("account.hub.mySubmissions")}</span>
          </button>
          <button className="bw-profile-stat" type="button" onClick={() => setActiveTab("orders")}>
            <strong>{pointsBalance.toLocaleString()}</strong>
            <span>{t("account.hub.availablePoints")}</span>
          </button>
          <button className="bw-profile-stat" type="button" onClick={() => setActiveTab("vip")}>
            <strong>{vip?.tier_label || t("account.hub.pending")}</strong>
            <span>{t("account.hub.vipStatus")}</span>
          </button>
        </div>
      </header>

      {flash ? <div className={`inline-message inline-message--${flash.tone}`}>{flash.body}</div> : null}

      <nav className="bw-profile-tabs" aria-label={t("account.hub.myAccount")}>
        {(["platforms", "submissions", "orders", "profile", "vip", "settings"] as AccountTab[]).map((tab) => (
          <button
            key={tab}
            type="button"
            className={`bw-profile-tab${activeTab === tab ? " is-active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tabIcon(tab)}
            <span>{t(`account.hub.tabs.${tab}`)}</span>
          </button>
        ))}
      </nav>

      {activeTab === "platforms" ? (
        <div className="bw-profile-pane">
          {platformRows.map(({ platform, account, verification }) => {
            const verificationStatus = String(verification?.status || "").trim().toLowerCase();
            const verificationCompleted =
              Boolean(account?.verified) ||
              verificationStatus === "verified" ||
              verificationStatus === "approved" ||
              verificationStatus === "approved_override";
            const pendingVerification =
              !verificationCompleted &&
              Boolean(
                account?.verify_code ||
                  verificationStatus === "pending" ||
                  verificationStatus === "awaiting_scan" ||
                  verificationStatus === "needs_review" ||
                  verificationStatus === "failed",
              );
            const commentText = verification?.generated_comment || verification?.note || "";
            return (
              <article key={platform} className="bw-platform-card">
                <div className="bw-platform-card__row">
                  <div className="bw-platform-card__icon" style={{ color: platformAccent(platform) }}>
                    <svg viewBox="0 0 24 24" fill="currentColor" stroke="none">
                      <circle cx="12" cy="12" r="10" />
                    </svg>
                  </div>
                  <div className="bw-platform-card__copy">
                    <strong>{account?.handle || platformLabel(platform)}</strong>
                    <small>{account ? platformDescription(account, t) : t("account.hub.platform.notConnected")}</small>
                    {!verificationCompleted && verification?.expires_at ? (
                      <span>{`${t("account.hub.expires")} ${formatDateTimeLabel(verification.expires_at, i18n.language)}`}</span>
                    ) : null}
                  </div>
                  <span className={`bw-status-pill bw-status-pill--${statusClassName(account)}`}>
                    {statusLabel(account, t)}
                  </span>
                </div>

                {account ? (
                  <div className="bw-platform-card__actions">
                    {!account.verified && account.verify_code ? (
                      <button className="bw-secondary-btn" type="button" onClick={() => void copyText(account.verify_code || "")}>
                        {t("account.hub.copyCode")}
                      </button>
                    ) : null}
                    {!account.verified ? (
                      <button className="bw-secondary-btn bw-secondary-btn--strong" type="button" onClick={() => void beginVerification(platform, account.handle)}>
                        {t("account.hub.startVerify")}
                      </button>
                    ) : null}
                    {verification?.status === "pending" ? (
                      <button className="bw-secondary-btn" type="button" onClick={() => void postedVerification(verification.id)}>
                        {t("account.hub.markPosted")}
                      </button>
                    ) : null}
                    <button className="bw-secondary-btn" type="button" onClick={() => void removePlatform(account.id)}>
                      {t("account.hub.remove")}
                    </button>
                  </div>
                ) : (
                  <div className="bw-platform-card__actions">
                    <button
                      className="bw-secondary-btn"
                      type="button"
                      onClick={() => setSocialDraft((current) => ({ ...current, platform }))}
                    >
                      {t("account.hub.useThisSlot")}
                    </button>
                  </div>
                )}

                {pendingVerification ? (
                  <div className="bw-verify-card">
                    <div className="bw-verify-step">
                      <span className="bw-verify-dot is-done">1</span>
                      <div>
                        <strong>{t("account.hub.verifyCode")}</strong>
                        <small>{account?.verify_code || "—"}</small>
                      </div>
                      {account?.verify_code ? (
                        <button className="bw-inline-chip" type="button" onClick={() => void copyText(account.verify_code || "")}>
                          {t("account.hub.copyCode")}
                        </button>
                      ) : null}
                    </div>
                    <div className="bw-verify-step">
                      <span className={`bw-verify-dot${commentText ? " is-active" : ""}`}>2</span>
                      <div>
                        <strong>{t("account.hub.verification")}</strong>
                        <small>{commentText || t("account.hub.flash.verificationStarted", { platform: platformLabel(platform) })}</small>
                      </div>
                      {commentText ? (
                        <button className="bw-inline-chip" type="button" onClick={() => void copyText(commentText)}>
                          {t("account.hub.copy")}
                        </button>
                      ) : null}
                    </div>
                    {verification?.status === "pending" ? (
                      <div className="bw-verify-step">
                        <span className="bw-verify-dot">3</span>
                        <div>
                          <strong>{t("account.hub.markPosted")}</strong>
                          <small>{t("account.hub.connectPlatformBody")}</small>
                        </div>
                        <button className="bw-inline-chip" type="button" onClick={() => void postedVerification(verification.id)}>
                          {t("account.hub.markPosted")}
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </article>
            );
          })}

          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.connectPlatformTitle")}</div>
            <div className="bw-settings-card bw-settings-card--form">
              <p className="bw-settings-copy">{t("account.hub.connectPlatformBody")}</p>
              <div className="bw-settings-grid bw-settings-grid--platform">
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
                <button className="bw-primary-btn" type="button" disabled={saving || !socialDraft.handle.trim()} onClick={() => void connectPlatform()}>
                  {saving ? t("account.hub.working") : t("account.hub.linkAccount")}
                </button>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "profile" ? (
        <div className="bw-settings-stack">
          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.myAccount")}</div>
            <div className="bw-settings-card bw-settings-card--form">
              <div className="bw-settings-grid bw-settings-grid--profile">
                <div className="mini-field">
                  <label>{t("account.hub.profile.avatarUrl")}</label>
                  <input className="input" value={avatarUrl} onChange={(event) => setAvatarUrl(event.target.value)} placeholder={t("account.hub.profile.avatarPlaceholder")} />
                </div>
                <div className="mini-field">
                  <label>{t("account.hub.profile.displayName")}</label>
                  <input className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder={t("account.hub.profile.namePlaceholder")} />
                </div>
                <div className="mini-field bw-settings-grid__full">
                  <label>{t("account.hub.profile.bio")}</label>
                  <textarea className="input" value={bio} onChange={(event) => setBio(event.target.value)} placeholder={t("account.hub.profile.bioPlaceholder")} rows={3} />
                </div>
              </div>
              <button className="bw-primary-btn bw-primary-btn--full" type="button" onClick={() => void saveProfile()} disabled={saving}>
                {saving ? t("account.hub.saving") : t("account.hub.saveProfile")}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "submissions" ? (
        <div className="bw-submission-grid">
          {submissions.length ? (
            submissions.map((submission) => {
              const score = Number(submission.overall_score ?? submission.final_score ?? 0);
              const preview = buildSubmissionViewerData(submission, creatorCode);
              const gear = submissionGear(submission);
              const gearMissing = gear === t("viewer.emptyValue");
              const scoreMetrics = [
                { key: "clean", label: t("account.hub.metrics.clean"), value: submissionCleanliness(submission) ?? "—" },
                { key: "speed", label: t("account.hub.metrics.speed"), value: submissionSpeed(submission) ?? "—" },
                { key: "quality", label: t("account.hub.metrics.quality"), value: submissionQuality(submission) ?? "—" },
              ];
              const hasMedia = Boolean(preview.uploadedVideoUrl || preview.posterUrl);
              const hasContext = Boolean(preview.externalLinks.length || preview.extraBody);
              const detailLabel = hasMedia
                ? t("account.hub.detailState.mediaReady")
                : hasContext
                  ? t("account.hub.detailState.summaryOnly")
                  : t("account.hub.detailState.statsOnly");
              const detailTone = hasMedia ? "media" : hasContext ? "summary" : "pending";
              return (
                <article key={submission.id} className="bw-submission-card">
                  <div className="bw-submission-card__top">
                    <div className="bw-submission-card__heading">
                      <strong>{submission.title || `Submission #${submission.id}`}</strong>
                      <div className="bw-submission-card__meta">
                        <span>{submissionDateLabel(submission.created_at)}</span>
                        <span>{submission.platform || t("account.hub.unknown")}</span>
                      </div>
                    </div>
                    <span className={`bw-status-pill bw-status-pill--${submissionStatusTone(submission.detection_status)}`}>
                      {submissionStatusLabel(submission.detection_status)}
                    </span>
                  </div>
                  <div className="bw-submission-card__summary">
                    <div className="bw-submission-card__metrics">
                      {scoreMetrics.map((metric) => (
                        <div key={`${submission.id}-${metric.key}`} className="bw-submission-metric-pill">
                          <small>{metric.label}</small>
                          <strong>{metric.value}</strong>
                        </div>
                      ))}
                    </div>
                    <div className="bw-submission-card__gear">
                      <span>{t("account.hub.gearLabel")}</span>
                      <strong className={gearMissing ? "is-muted" : ""}>{gear}</strong>
                    </div>
                  </div>
                  <div className="bw-submission-card__foot">
                    <div className="bw-submission-card__foot-left">
                      <span className={`bw-score-pill bw-score-pill--${scoreClass(score)}`}>
                        {score > 0 ? score : "—"}
                      </span>
                      <small className={`bw-submission-card__detail-note is-${detailTone}`}>{detailLabel}</small>
                    </div>
                    <div className="bw-submission-card__foot-right">
                      <small>
                        +{Number(submission.points_awarded || 0).toLocaleString()} {t("account.hub.pts")}
                      </small>
                      <button type="button" className="bw-inline-chip" onClick={() => setViewerData(preview)}>
                        {t("account.hub.view")}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })
          ) : (
            <div className="legacy-empty-row">{t("account.hub.noSubmissions")}</div>
          )}
        </div>
      ) : null}

      {activeTab === "orders" ? (
        <div className="bw-order-list">
          {redemptions.length ? (
            redemptions.map((record) => (
              <article key={record.id} className="bw-order-row">
                <div className="bw-order-row__icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M16 16H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h10l2 3v8a1 1 0 0 1-1 1z" />
                    <path d="M12 17v4m-4-4h8" />
                  </svg>
                </div>
                <div className="bw-order-row__copy">
                  <strong>{record.item_name || `Reward #${record.id}`}</strong>
                  <small>
                    {record.created_at || t("account.hub.pendingDate")} · -{Number(record.points_cost || 0).toLocaleString()} {t("account.hub.pts")}
                    {record.tracking_number ? ` · ${record.tracking_number}` : ""}
                  </small>
                </div>
                <span className={`bw-status-pill bw-status-pill--${normalizePlatform(record.status || "processing")}`}>
                  {record.status || t("account.hub.pending")}
                </span>
              </article>
            ))
          ) : (
            <div className="legacy-empty-row">{t("account.hub.noOrders")}</div>
          )}
        </div>
      ) : null}

      {activeTab === "vip" ? (
        <div className="bw-settings-stack">
          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.vipStatus")}</div>
            <div className="bw-settings-card">
              <div className="bw-settings-row">
                <span>{t("account.hub.vipStatus")}</span>
                <b>{vip?.tier_label || t("account.hub.pending")}</b>
              </div>
              <div className="bw-settings-row">
                <span>{t("account.hub.availablePoints")}</span>
                <b>{pointsBalance.toLocaleString()} {t("account.hub.pts")}</b>
              </div>
              <div className="bw-settings-row">
                <span>{t("account.hub.affiliateLink")}</span>
                <div className="bw-settings-row__inline">
                  <b>{affiliate?.ref_code || "—"}</b>
                  {affiliateLink ? (
                    <button className="bw-inline-chip" type="button" onClick={() => void copyText(affiliateLink)}>
                      {t("account.hub.copy")}
                    </button>
                  ) : null}
                </div>
              </div>
              <div className="bw-settings-block">
                <span className="bw-settings-block__label">{t("account.hub.shopifyStoreLink")}</span>
                <div className="bw-affiliate-link-row">
                  <input
                    className="bw-affiliate-link-input"
                    readOnly
                    value={affiliateLink}
                    placeholder={t("account.hub.affiliatePlaceholder")}
                  />
                  <div className="bw-settings-row__inline">
                    <button className="bw-inline-chip" type="button" disabled={!affiliateLink} onClick={() => void copyText(affiliateLink)}>
                      {t("account.hub.copy")}
                    </button>
                    <button
                      className="bw-inline-chip"
                      type="button"
                      disabled={!affiliateLink}
                      onClick={() => {
                        if (affiliateLink) {
                          window.open(affiliateLink, "_blank", "noopener,noreferrer");
                        }
                      }}
                    >
                      {t("viewer.openLink")}
                    </button>
                  </div>
                </div>
              </div>
              <div className="bw-settings-metric-grid">
                <div className="bw-settings-metric-card">
                  <small>{t("account.hub.trackedOrdersLabel")}</small>
                  <strong>{affiliateOrders.toLocaleString()}</strong>
                  <span>{t("account.hub.trackedOrders", { count: affiliateOrders.toLocaleString() })}</span>
                </div>
                <div className="bw-settings-metric-card">
                  <small>{t("account.hub.gmvLabel")}</small>
                  <strong>{moneyFormatter.format(affiliateRevenue)}</strong>
                  <span>{t("account.hub.shopifyStatusLabel")}: {affiliate?.shopify_signal_ready ? t("account.hub.shopifyReady") : t("account.hub.shopifyWaiting")}</span>
                </div>
                <div className="bw-settings-metric-card">
                  <small>{t("account.hub.estimatedCommissionLabel")}</small>
                  <strong>{moneyFormatter.format(estimatedCommission)}</strong>
                  <span>{t("account.hub.commissionRateLabel", { rate: Math.round(effectiveCommissionRate * 100) })}</span>
                </div>
              </div>
              {hasStudentLane ? (
                <div className="bw-settings-row">
                  <span>{t("account.hub.studentPass")}</span>
                  <div className="bw-settings-row__inline">
                    <b>{studentPass?.student_id_code || studentProgram?.student_id_code || t("account.hub.studentPassFallback")}</b>
                    {studentPass?.pass_url ? (
                      <button className="bw-inline-chip" type="button" onClick={() => void copyText(studentPass.pass_url || "")}>
                        {t("account.hub.copyPassUrl")}
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : null}
              <div className="bw-settings-copy bw-settings-copy--tight">
                {vip?.activation_message || affiliate?.activation_message || t("account.hub.affiliateMeta")}
              </div>
              {trust ? (
                <div className="bw-settings-copy bw-settings-copy--tight">
                  {t("account.hub.trustPill", {
                    score: Math.round(Number(trust.score || 0)),
                    label: trust.label || t("account.hub.normal"),
                  })}
                </div>
              ) : null}
            </div>
          </section>

          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.vipUpgradeTitle")}</div>
            <div className="bw-vip-ladder">
              {vipUpgradeTiers.map((tier) => (
                <article key={tier.key} className={`bw-vip-ladder-card${vip?.tier_key === tier.key ? " is-active" : ""}`}>
                  <div className="bw-vip-ladder-card__head">
                    <strong>{tier.label}</strong>
                    <span>{tier.multiplier}</span>
                  </div>
                  <p>{t("account.hub.vipUpgradeRule", {
                    points: tier.points.toLocaleString(),
                    videos: tier.videos,
                    commission: tier.commission,
                  })}</p>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "settings" ? (
        <div className="bw-settings-stack">
          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.password.kicker")}</div>
            <div className="bw-settings-card bw-settings-card--form">
              <div className="bw-settings-panel__head">
                <strong>{t("account.hub.password.title")}</strong>
                <p>{t("account.hub.password.body")}</p>
              </div>
              <div className="bw-settings-grid">
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
              <button className="bw-secondary-btn bw-secondary-btn--strong bw-primary-btn--full" type="button" onClick={() => void savePassword()} disabled={passwordSaving}>
                {passwordSaving ? t("account.hub.saving") : t("account.hub.password.action")}
              </button>
            </div>
          </section>

          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.addressesTitle")}</div>
            <div className="bw-settings-card bw-settings-card--form">
              <div className="bw-settings-panel">
                <div className="bw-settings-panel__row">
                  <div className="bw-settings-panel__head">
                    <strong>{t("account.hub.savedAddresses")}</strong>
                    <p>{t("account.hub.savedAddressesBody")}</p>
                  </div>
                  <button className="bw-secondary-btn bw-settings-panel__cta" type="button" onClick={openAddressComposer}>
                    {t("account.hub.newAddressAction")}
                  </button>
                </div>
                {addressRows.length ? (
                  <div className="bw-address-list">
                    {addressRows.map((address) => (
                      <div key={address.id} className={`bw-address-row${address.is_default ? " is-default" : ""}`}>
                        <div className="bw-address-row__body">
                          <strong>{address.name || t("account.hub.primaryRecipient")}</strong>
                          <small>
                            {[address.address1, address.address2, address.city, address.state, address.postal_code, address.country]
                              .filter(Boolean)
                              .join(", ")}
                          </small>
                          {address.phone ? <span>{address.phone}</span> : null}
                        </div>
                        <div className="bw-address-row__actions">
                          {address.is_default ? <span className="bw-status-pill bw-status-pill--verified">{t("account.hub.default")}</span> : null}
                          {!address.is_default ? (
                            <button className="bw-inline-chip" type="button" onClick={() => void makeDefaultAddress(address.id)}>
                              {t("account.hub.makeDefault")}
                            </button>
                          ) : null}
                          <button className="bw-inline-chip bw-inline-chip--danger" type="button" onClick={() => void removeAddress(address.id)}>
                            {t("account.hub.deleteAddress")}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="legacy-empty-row">{t("account.hub.noAddresses")}</div>
                )}
              </div>
            </div>
          </section>

          <section className="bw-settings-section">
            <div className="bw-settings-label">{t("account.hub.signOut")}</div>
            <div className="bw-settings-card bw-settings-card--form">
              <p className="bw-settings-copy">{t("account.hub.subtitle")}</p>
              <button className="bw-danger-btn bw-primary-btn--full" type="button" onClick={onSignOut}>
                {t("account.hub.signOut")}
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {addressComposerOpen ? (
        <div className="bw-modal-backdrop" role="presentation" onClick={closeAddressComposer}>
          <div className="bw-address-modal" role="dialog" aria-modal="true" aria-labelledby="bw-address-modal-title" onClick={(event) => event.stopPropagation()}>
            <div className="bw-address-modal__head">
              <div className="bw-settings-panel__head">
                <strong id="bw-address-modal-title">{t("account.hub.newAddressTitle")}</strong>
                <p>{t("account.hub.addShippingAddressBody")}</p>
              </div>
            </div>

            <div className="bw-settings-grid bw-settings-grid--address bw-address-modal__grid">
              <div className="mini-field">
                <label>{t("account.hub.address.recipient")}</label>
                <input className="input" value={addressDraft.name} onChange={(event) => setAddressDraft({ ...addressDraft, name: event.target.value })} placeholder={t("account.hub.address.recipient")} />
              </div>
              <div className="mini-field">
                <label>{t("account.hub.address.phone")}</label>
                <input className="input" value={addressDraft.phone} onChange={(event) => setAddressDraft({ ...addressDraft, phone: event.target.value })} placeholder={t("account.hub.address.phone")} />
              </div>
              <div className="mini-field bw-settings-grid__full">
                <label>{t("account.hub.address.line1")}</label>
                <input className="input" value={addressDraft.address1} onChange={(event) => setAddressDraft({ ...addressDraft, address1: event.target.value })} placeholder={t("account.hub.address.line1")} />
              </div>
              <div className="mini-field bw-settings-grid__full">
                <label>{t("account.hub.address.line2")}</label>
                <input className="input" value={addressDraft.address2} onChange={(event) => setAddressDraft({ ...addressDraft, address2: event.target.value })} placeholder={t("account.hub.address.line2")} />
              </div>
              <div className="mini-field">
                <label>{t("account.hub.address.city")}</label>
                <input className="input" value={addressDraft.city} onChange={(event) => setAddressDraft({ ...addressDraft, city: event.target.value })} placeholder={t("account.hub.address.city")} />
              </div>
              <div className="mini-field">
                <label>{t("account.hub.address.state")}</label>
                <input className="input" value={addressDraft.state} onChange={(event) => setAddressDraft({ ...addressDraft, state: event.target.value })} placeholder={t("account.hub.address.state")} />
              </div>
              <div className="mini-field">
                <label>{t("account.hub.address.postalCode")}</label>
                <input className="input" value={addressDraft.postal_code} onChange={(event) => setAddressDraft({ ...addressDraft, postal_code: event.target.value })} placeholder={t("account.hub.address.postalCode")} />
              </div>
              <div className="mini-field">
                <label>{t("account.hub.address.country")}</label>
                <input className="input" value={addressDraft.country} onChange={(event) => setAddressDraft({ ...addressDraft, country: event.target.value })} placeholder={t("account.hub.address.country")} />
              </div>
            </div>

            <div className="bw-address-modal__actions">
              <button className="bw-secondary-btn" type="button" onClick={closeAddressComposer}>
                {t("account.hub.cancel")}
              </button>
              <button className="bw-primary-btn" type="button" onClick={() => void addAddress()} disabled={saving || !addressDraft.address1 || !addressDraft.city}>
                {saving ? t("account.hub.saving") : t("account.hub.saveAddress")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {qrModalOpen && publicShareCardUrl ? (
        <div className="vid-qr-modal-backdrop" role="presentation" onClick={closeProfileQrModal}>
          <section className="vid-qr-modal vid-qr-modal--share-card" role="dialog" aria-modal="true" aria-labelledby="bw-profile-qr-title" onClick={(event) => event.stopPropagation()}>
            <button className="vid-qr-modal-close" type="button" onClick={closeProfileQrModal} aria-label="Close QR code">
              ×
            </button>
            <small>Viltrox Creator QR</small>
            <h2 id="bw-profile-qr-title">{creatorCode}</h2>
            <img className="vid-share-card-preview" src={publicShareCardUrl} alt={`${creatorCode} Viltrox QR card`} />
            <div className="vid-qr-modal-actions">
              {publicProfileUrl ? (
                <a href={publicProfileUrl} target="_blank" rel="noreferrer">
                  打开公开页
                </a>
              ) : null}
              <a href={publicShareCardDownloadUrl} download={`${creatorCode}-viltrox-qr-card.png`}>
                下载 QR 卡片
              </a>
              <AppleWalletButton compact onClick={handleWalletClick} />
            </div>
            {qrWalletMessage ? <div className="vid-qr-modal-note">{qrWalletMessage}</div> : null}
          </section>
        </div>
      ) : null}

      <LegacyVideoViewer open={Boolean(viewerData)} data={viewerData} onClose={() => setViewerData(null)} />
    </div>
  );
}
