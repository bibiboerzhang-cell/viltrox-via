import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { AccountHub } from "../components/account/AccountHub";
import { EmptyState } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import {
  apiFetch,
  type CreatorAddress,
  type CreatorAddressResponse,
  type CreatorProgramResponse,
  type CreatorRedemptionsResponse,
  type CreatorSocialAccount,
  type CreatorSocialAccountsResponse,
  type CreatorSubmission,
  type CreatorSubmissionsResponse,
  type RedemptionRecord,
} from "../lib/api";

export default function AccountRoute() {
  const navigate = useNavigate();
  const { status, token, user, refreshUser, signOut } = useAuth();
  const [addresses, setAddresses] = useState<CreatorAddress[]>([]);
  const [socialAccounts, setSocialAccounts] = useState<CreatorSocialAccount[]>([]);
  const [submissions, setSubmissions] = useState<CreatorSubmission[]>([]);
  const [redemptions, setRedemptions] = useState<RedemptionRecord[]>([]);
  const [program, setProgram] = useState<CreatorProgramResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== "authenticated" || !token) {
      setLoading(false);
      return;
    }

    let mounted = true;

    async function loadAccount() {
      setLoading(true);
      try {
        const [addressesResponse, socialResponse, submissionsResponse, redemptionsResponse, programResponse] =
          await Promise.all([
            apiFetch<CreatorAddressResponse>("/api/creator/addresses", {}, token),
            apiFetch<CreatorSocialAccountsResponse>("/api/creator/social-accounts", {}, token),
            apiFetch<CreatorSubmissionsResponse>("/api/creator/submissions?limit=50", {}, token),
            apiFetch<CreatorRedemptionsResponse>("/api/creator/redemptions", {}, token),
            apiFetch<CreatorProgramResponse>("/api/creator/program", {}, token),
          ]);

        if (!mounted) {
          return;
        }

        setAddresses(addressesResponse.addresses || []);
        setSocialAccounts(socialResponse.accounts || []);
        setSubmissions(submissionsResponse.submissions || []);
        setRedemptions(redemptionsResponse.redemptions || []);
        setProgram(programResponse);
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
  }, [status, token]);

  return (
    <div className="account-route-shell">
      <div className="account-route-backdrop" />
      <div className="account-route-stage">
        {status !== "authenticated" || !user ? (
          <div className="sheet account-sheet">
            <div className="sheet-top">
              <div>
                <h3>Account Access</h3>
                <p>Sign in or create an account, then open your account panel to manage profile, addresses and platform verification.</p>
              </div>
              <button className="close" type="button" onClick={() => navigate("/react")}>
                ×
              </button>
            </div>
            <EmptyState
              title="Sign in required"
              body="The account dashboard needs a creator session before it can load profile, submissions, and linked platform history."
              action={
                <button className="cta" type="button" onClick={() => navigate("/login")}>
                  Go to sign in
                </button>
              }
            />
          </div>
        ) : loading ? (
          <div className="sheet account-sheet">
            <div className="sheet-top">
              <div>
                <h3>Account Access</h3>
                <p>Loading your creator panel...</p>
              </div>
              <button className="close" type="button" onClick={() => navigate("/react")}>
                ×
              </button>
            </div>
            <div className="muted-block">Loading account data...</div>
          </div>
        ) : (
          <AccountHub
            user={user}
            token={token}
            addresses={addresses}
            socialAccounts={socialAccounts}
            submissions={submissions}
            redemptions={redemptions}
            program={program}
            onSaved={refreshUser}
            onSignOut={async () => {
              await signOut();
              navigate("/react");
            }}
            onClose={() => navigate("/react")}
          />
        )}
      </div>
    </div>
  );
}
