import { apiFetch } from "./http";
import type {
  BasicStatusResponse,
  CreatorAddress,
  CreatorAddressResponse,
  CreatorProgramResponse,
  CreatorRedemptionsResponse,
  CreatorSocialAccount,
  CreatorSocialAccountsResponse,
  CreatorSubmission,
  CreatorSubmissionsResponse,
  RedemptionRecord,
} from "../types/api";

export interface CreatorAccountBundle {
  addresses: CreatorAddress[];
  socialAccounts: CreatorSocialAccount[];
  submissions: CreatorSubmission[];
  redemptions: RedemptionRecord[];
  program: CreatorProgramResponse | null;
}

export async function listCreatorAddresses(token: string): Promise<CreatorAddress[]> {
  const response = await apiFetch<CreatorAddressResponse>("/api/creator/addresses", {}, token);
  return response.addresses || [];
}

export async function listCreatorSocialAccounts(token: string): Promise<CreatorSocialAccount[]> {
  const response = await apiFetch<CreatorSocialAccountsResponse>("/api/creator/social-accounts", {}, token);
  return response.accounts || [];
}

export async function listCreatorSubmissions(token: string): Promise<CreatorSubmission[]> {
  const response = await apiFetch<CreatorSubmissionsResponse>("/api/creator/submissions?limit=50", {}, token);
  return response.submissions || [];
}

export async function listCreatorRedemptions(token: string): Promise<RedemptionRecord[]> {
  const response = await apiFetch<CreatorRedemptionsResponse>("/api/creator/redemptions", {}, token);
  return response.redemptions || [];
}

export async function listCreatorProgram(token: string): Promise<CreatorProgramResponse | null> {
  return apiFetch<CreatorProgramResponse>("/api/creator/program", {}, token);
}

export async function loadCreatorAccountBundle(token: string): Promise<CreatorAccountBundle> {
  const [addresses, socialAccounts, submissions, redemptions, program] = await Promise.all([
    listCreatorAddresses(token),
    listCreatorSocialAccounts(token),
    listCreatorSubmissions(token),
    listCreatorRedemptions(token),
    listCreatorProgram(token),
  ]);
  return {
    addresses,
    socialAccounts,
    submissions,
    redemptions,
    program,
  };
}

export function addCreatorSocialAccount(token: string, payload: { platform?: string; handle: string }) {
  return apiFetch<BasicStatusResponse & {
    verify_code?: string;
    platform?: string;
    handle?: string;
    profile_url?: string;
  }>(
    "/api/creator/social-accounts",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    token,
  );
}

export function deleteCreatorSocialAccount(token: string, accountId: number) {
  return apiFetch<BasicStatusResponse>(`/api/creator/social-accounts/${accountId}`, { method: "DELETE" }, token);
}

export function createCreatorAddress(
  token: string,
  payload: {
    name: string;
    phone?: string;
    address1: string;
    address2?: string;
    city: string;
    state?: string;
    country?: string;
    postal_code?: string;
  },
) {
  return apiFetch<BasicStatusResponse>("/api/creator/addresses", {
    method: "POST",
    body: JSON.stringify(payload),
  }, token);
}

export function setDefaultAddress(token: string, addressId: number) {
  return apiFetch<BasicStatusResponse>(`/api/creator/addresses/${addressId}/default`, { method: "PATCH" }, token);
}

export function deleteCreatorAddress(token: string, addressId: number) {
  return apiFetch<BasicStatusResponse>(`/api/creator/addresses/${addressId}`, { method: "DELETE" }, token);
}
