import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";

import { BwAccountHub, type AccountTab } from "../../components/account/BwAccountHub";
import { FloatingViaCat } from "../../components/catographer/FloatingViaCat";
import { BwTopNav, EmptyState } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import {
  loadCreatorAccountBundle,
} from "../../services/creator.service";
import type {
  CreatorAddress,
  CreatorProgramResponse,
  CreatorSocialAccount,
  CreatorSubmission,
  RedemptionRecord,
} from "../../types/api";

export default function AccountRoute() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const { status, token, user, refreshUser, signOut, openAuthModal } = useAuth();
  const [addresses, setAddresses] = useState<CreatorAddress[]>([]);
  const [socialAccounts, setSocialAccounts] = useState<CreatorSocialAccount[]>([]);
  const [submissions, setSubmissions] = useState<CreatorSubmission[]>([]);
  const [redemptions, setRedemptions] = useState<RedemptionRecord[]>([]);
  const [program, setProgram] = useState<CreatorProgramResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const initialTab = useMemo<AccountTab>(() => {
    const tab = String(searchParams.get("tab") || "").trim().toLowerCase();
    if (tab === "addresses") {
      return "settings";
    }
    return ["platforms", "submissions", "orders", "profile", "vip", "settings"].includes(tab)
      ? (tab as AccountTab)
      : "platforms";
  }, [searchParams]);

  useEffect(() => {
    if (status !== "authenticated" || !token) {
      setLoadError(null);
      setLoading(false);
      return;
    }

    let mounted = true;
    async function loadAccount() {
      setLoading(true);
      setLoadError(null);
      try {
        const bundle = await loadCreatorAccountBundle(token);
        if (!mounted) {
          return;
        }
        setAddresses(bundle.addresses);
        setSocialAccounts(bundle.socialAccounts);
        setSubmissions(bundle.submissions);
        setRedemptions(bundle.redemptions);
        setProgram(bundle.program);
      } catch (error) {
        if (!mounted) {
          return;
        }
        setAddresses([]);
        setSocialAccounts([]);
        setSubmissions([]);
        setRedemptions([]);
        setProgram(null);
        setLoadError(error instanceof Error ? error.message : t("account.loading"));
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    }

    void loadAccount();
    return () => {
      mounted = false;
    };
  }, [status, t, token, retryNonce]);

  useEffect(() => {
    if (status !== "authenticated" || !user || !program?.vip) {
      return;
    }
    const nextPoints = Number(program.vip.current_points ?? user.points_total ?? 0);
    const currentPoints = Number(user.points_total || 0);
    if (nextPoints !== currentPoints) {
      void refreshUser();
    }
  }, [program?.vip, refreshUser, status, user]);

  const accountPoints = user
    ? Number(user.points_balance ?? 0)
    : undefined;

  return (
    <div className="bw-app bw-app--account">
      <BwTopNav active="account" user={user} points={accountPoints} />
      <main className="bw-page bw-page--account">
        {status !== "authenticated" || !user ? (
          <section className="bw-empty-surface">
            <EmptyState
              title={t("account.signInRequiredTitle")}
              body={t("account.signInRequiredBody")}
              action={
                <button className="bw-primary-btn" type="button" onClick={() => openAuthModal("signin")}>
                  {t("account.signInRequiredAction")}
                </button>
              }
            />
          </section>
        ) : loading ? (
          <section className="bw-empty-surface">
            <div className="muted-block">{t("account.loading")}</div>
          </section>
        ) : loadError ? (
          <section className="bw-empty-surface">
            <EmptyState
              title={t("account.title")}
              body={loadError}
              action={
                <button className="bw-primary-btn" type="button" onClick={() => setRetryNonce((value) => value + 1)}>
                  {t("account.retryLoad")}
                </button>
              }
            />
          </section>
        ) : (
          <BwAccountHub
            user={user}
            token={token}
            addresses={addresses}
            socialAccounts={socialAccounts}
            submissions={submissions}
            redemptions={redemptions}
            program={program}
            initialTab={initialTab}
            onSaved={refreshUser}
            onSignOut={async () => {
              await signOut();
              navigate("/");
            }}
          />
        )}

      </main>
      <FloatingViaCat />
    </div>
  );
}
