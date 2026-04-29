export interface AuthUser {
  id: number;
  email: string;
  name: string;
  creator_code?: string;
  role?: string;
  points_balance?: number;
  points_pending?: number;
  points_total?: number;
  avatar_url?: string;
  bio?: string;
  permissions?: Record<string, "none" | "read" | "write" | string>;
  is_owner?: boolean;
}

export interface LoginResponse {
  status: string;
  message?: string;
  token?: string;
  user?: AuthUser;
}

export interface BasicStatusResponse {
  status: string;
  message?: string;
}

export interface MeResponse {
  status: string;
  message?: string;
  user?: AuthUser;
}

export interface RegisterResponse extends BasicStatusResponse {
  token?: string;
  user?: AuthUser;
}

export interface UploadResponse {
  status: string;
  message?: string;
  video_id?: string;
  asset_id?: number;
  r2_key?: string;
  size_mb?: number;
  filename?: string;
  mime_type?: string;
  duplicate?: Record<string, unknown>;
}

export interface AuditResponse {
  status: string;
  job_id?: string;
  analysis_task_id?: string;
  rejection_code?: string;
  rejection_reason?: string;
}

export interface RewardItem {
  id: number;
  title: string;
  category: string;
  description?: string;
  points_cost: number;
  image_url?: string;
  meta_label?: string;
  stock?: number;
  sort_order?: number;
  status?: string;
}

export interface RewardsResponse {
  rewards: RewardItem[];
}

export interface ExternalLink {
  label?: string;
  url: string;
}

export interface LeaderboardEntry {
  user_id?: number;
  display_name?: string;
  name?: string;
  creator_code?: string;
  handle?: string;
  platform?: string;
  points?: number;
  estimated_points?: number;
  total_score?: number;
  top_score?: number;
  total_campaign_score?: number;
  total_points_earned?: number;
  confirmed?: number;
  submission_count?: number;
  submissions?: number;
  platforms?: string;
  uploaded_video_url?: string;
  poster_url?: string;
  gear_tag?: string;
  external_links?: ExternalLink[];
}

export interface LeaderboardResponse {
  items: LeaderboardEntry[];
}

export interface CreatorAddress {
  id: number;
  name?: string;
  phone?: string;
  address1?: string;
  address2?: string;
  city?: string;
  state?: string;
  postal_code?: string;
  country?: string;
  is_default?: number | boolean;
}

export interface CreatorAddressResponse {
  addresses: CreatorAddress[];
}

export interface CreatorSocialAccount {
  id: number;
  platform: string;
  handle: string;
  verified?: number | boolean;
  verify_code?: string;
}

export interface CreatorSocialAccountsResponse {
  accounts: CreatorSocialAccount[];
}

export interface VerificationPreviewResponse {
  requested_platform?: string | null;
  platform: string;
  handle: string;
  profile_url: string;
  platform_match: boolean;
  protected_handle: boolean;
}

export interface VerificationStartResponse {
  verification_id: number;
  social_account_id?: number | null;
  platform: string;
  handle: string;
  profile_url: string;
  code: string;
  expires_at: string;
  generated_comment: string;
  viltrox_account_url: string;
  viltrox_account_name: string;
  instructions: string[];
  status: string;
  comment_source?: string | null;
  comment_job_id?: string | null;
}

export interface VerificationRecord {
  id: number;
  platform: string;
  handle: string;
  status: string;
  match_score?: number | null;
  scan_count?: number;
  created_at?: string;
  last_scanned_at?: string | null;
  generated_comment?: string;
  expires_at?: string | null;
  note?: string | null;
  comment_id?: string | null;
  comment_job_id?: string | null;
}

export interface VerificationListResponse {
  items?: VerificationRecord[];
  verifications?: VerificationRecord[];
}

export interface CreatorSubmission {
  id: number;
  created_at?: string;
  platform?: string;
  title?: string;
  detection_status?: string;
  final_score?: number;
  overall_score?: number;
  recommendation?: string;
  views?: number;
  likes?: number;
  product_label?: string;
  video_path?: string;
  url?: string;
  video_url?: string;
  best_frame_url?: string;
  poster_url?: string;
  has_video?: boolean;
  asset_key?: string;
  video_analysis?: Record<string, unknown>;
  points_awarded?: number;
}

export interface CreatorSubmissionsResponse {
  submissions: CreatorSubmission[];
}

export interface RedemptionRecord {
  id: number;
  item_name?: string;
  status?: string;
  points_cost?: number;
  tracking_number?: string;
  created_at?: string;
}

export interface CreatorRedemptionsResponse {
  redemptions: RedemptionRecord[];
}

export interface CreatorProgramVipSnapshot {
  tier_status?: string;
  is_active?: boolean;
  tier_key?: string;
  tier_label?: string;
  badge_text?: string;
  current_points?: number;
  confirmed_videos?: number;
  threshold_points?: number;
  threshold_videos?: number;
  commission_rate?: number;
  points_multiplier?: number;
  next_tier_key?: string;
  next_tier_label?: string;
  next_threshold_points?: number;
  next_threshold_videos?: number;
  points_to_next?: number;
  videos_to_next?: number;
  progress_ratio?: number;
  points_progress_ratio?: number;
  video_progress_ratio?: number;
  is_top_tier?: boolean;
  activation_message?: string;
}

export interface CreatorProgramAffiliateSnapshot {
  affiliate_link?: string;
  preview_link?: string;
  ref_code?: string;
  orders_count?: number;
  revenue_total?: number;
  discount_total?: number;
  quantity_total?: number;
  last_order_at?: string;
  effective_commission_rate?: number;
  is_ready?: boolean;
  is_active?: boolean;
  shopify_signal_ready?: boolean;
  activation_message?: string;
}

export interface CreatorProgramTrustSnapshot {
  score?: number;
  label?: string;
  band_key?: string;
  limits?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
}

export interface CreatorProgramResponse {
  status?: string;
  vip?: CreatorProgramVipSnapshot;
  affiliate?: CreatorProgramAffiliateSnapshot;
  student?: {
    status?: string;
    school_id?: string;
    school_name?: string;
    student_id_code?: string;
    expires_at?: string;
    commission_rate_override?: number;
    is_active?: boolean;
  };
  identity_cards?: {
    student_cards?: Array<{
      qr_id?: string;
      school_id?: string;
      display_serial?: string;
      public_vid?: string;
      status?: string;
      card_image_url?: string;
      claim_url?: string;
      bound_at?: string;
    }>;
    total_cards?: number;
  };
  trust?: CreatorProgramTrustSnapshot;
  wardrobe?: Array<Record<string, unknown>>;
}

export interface StudentClaimMetadataResponse {
  status: string;
  claim_status?: string;
  school?: {
    school_id?: string;
    school_code?: string;
    school_name?: string;
    country?: string;
    region?: string;
    visual_theme?: Record<string, unknown>;
  };
  prefilled?: {
    name?: string;
    email?: string;
    student_id?: string;
    school_student_id?: string;
    major?: string;
    year?: string;
  };
  public_claim_id?: string;
  requirements?: {
    student_id?: boolean;
    student_email?: boolean;
    student_email_domains?: string[];
  };
  qr?: Record<string, unknown>;
  card_image_url?: string;
}

export interface StudentSignupResponse extends LoginResponse {
  student?: {
    status?: string;
    school_id?: string;
    school_name?: string;
    student_id_code?: string;
    vid?: string;
    school_student_id?: string;
    expires_at?: string;
    commission_rate_override?: number;
  };
  claim?: {
    qr_id?: string;
    display_serial?: string;
    vid?: string;
    school_id?: string;
  };
}

export interface PublicVidProfileResponse {
  status: string;
  vid: string;
  claim_status?: string;
  is_bound?: boolean;
  school?: {
    school_id?: string;
    school_code?: string;
    school_name?: string;
  };
  creator?: {
    id?: number;
    name?: string;
    creator_code?: string;
  };
  links?: {
    shop?: string;
    via?: string;
    signup?: string;
    qr?: string;
    share_card?: string;
    apple_wallet?: string;
  };
  accounts?: Array<{
    platform?: string;
    handle?: string;
    profile_url?: string;
    verified?: boolean;
    verified_at?: string;
  }>;
  submissions?: Array<{
    id: number;
    created_at?: string;
    platform?: string;
    url?: string;
    media_url?: string;
    poster_url?: string;
    handle?: string;
    title?: string;
    status?: string;
    product_series?: string;
    product_label?: string;
    score?: number;
    views?: number;
    likes?: number;
    comments?: number;
    shares?: number;
    points?: number;
  }>;
}

export interface VideoItem {
  id: number;
  title: string;
  platform?: string;
  url?: string;
  mediaUrl?: string;
  posterUrl?: string;
  handle?: string;
  status?: string;
  productSeries?: string;
  productLabel?: string;
  score?: number;
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  points?: number;
  createdAt?: string;
}

export interface ShopHero {
  id: string;
  title: string;
  subtitle?: string;
  imageUrl: string;
  targetUrl: string;
  badge?: string;
  source?: "manual" | "official_site";
  isActive?: boolean;
  sortOrder?: number;
}

export interface CreatorPublicPageData {
  status: string;
  vid?: string;
  isBound?: boolean;
  creator: {
    id: string;
    name: string;
    code: string;
    avatarUrl?: string;
  };
  featuredVideos: VideoItem[];
  shopHeroes: ShopHero[];
  accounts?: Array<{
    platform?: string;
    handle?: string;
    profile_url?: string;
    verified?: boolean;
    verified_at?: string;
  }>;
  links?: {
    shop?: string;
    via?: string;
    signup?: string;
    qr?: string;
    share_card?: string;
    apple_wallet?: string;
  };
}

export interface CreatorPublicClickPayload {
  creator_id: string | number;
  creator_code: string;
  type: "shop_click";
  target_url: string;
  shop_hero_id: string;
}

export interface PublicVidShareCardResponse {
  status: string;
  vid: string;
  image_url?: string;
  target_url?: string;
  wallet_ready?: boolean;
  apple_wallet_url?: string;
  qr_url?: string;
}

export interface StudentPassResponse {
  status: string;
  token?: string;
  signature?: string;
  expires_at?: string;
  pass_url?: string;
  qr_data_uri?: string;
  student_id_code?: string;
  school_id?: string;
}

export interface AdminStats {
  total_users?: number;
  total_submissions?: number;
  pending_submissions?: number;
  confirmed_submissions?: number;
  total_points_awarded?: number;
  total?: number;
  confirmed?: number;
  suspected?: number;
  not_detected?: number;
  avg_final_score?: number;
  avg_creator_score?: number;
  total_views?: number;
  total_likes?: number;
  total_comments?: number;
  total_shares?: number;
  total_favorites?: number;
  unique_creators?: number;
  pending_verifications?: number;
  by_date?: Array<{ date?: string; day?: string; count?: number; n?: number }>;
  by_platform?: Array<{ platform?: string; count?: number; n?: number }>;
  by_series?: Array<{ series?: string; product_series?: string; count?: number; n?: number }>;
  by_status?: Array<{ status?: string; count?: number; n?: number }>;
  top_scores?: Array<Record<string, unknown>>;
  generated_at?: string;
}

export interface AdminSubmission {
  id: number;
  title?: string;
  platform?: string;
  detection_status?: string;
  created_at?: string;
  overall_score?: number;
  final_score?: number;
  creator_score?: number;
  risk_score?: number;
  points_awarded?: number;
  points_status?: string;
  creator_code?: string;
  extracted_handle?: string;
  user_name?: string;
  user_email?: string;
  display_name?: string;
  url?: string;
  memo?: string;
  recommendation?: string;
  product_series?: string;
  product_label?: string;
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  favorites?: number;
  video_analysis?: Record<string, unknown> | string;
  tech_score?: number;
  marketing_score?: number;
  content_genre?: string;
  vertical_category?: string;
}

export interface AdminSubmissionsResponse {
  items: AdminSubmission[];
  total?: number;
}

export interface AdminRewardsResponse {
  items: RewardItem[];
  rewards?: RewardItem[];
}

export type SystemHealthSnapshot = Record<string, unknown>;

const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env ?? {};
const configuredBase = String(env.VITE_API_BASE ?? "").trim().replace(/\/+$/, "");

export const API_BASE = configuredBase;

export function buildApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return API_BASE ? `${API_BASE}${normalized}` : normalized;
}

export function buildEventStreamUrl(jobId: string): string {
  return buildApiUrl(`/api/audit/stream/${jobId}`);
}

export function jsonBody(payload: unknown): string {
  return JSON.stringify(payload);
}

const DEFAULT_API_TIMEOUT_MS = 20000;
const DEFAULT_UPLOAD_TIMEOUT_MS = 10 * 60 * 1000;

export async function apiFetch<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
  token?: string,
): Promise<T> {
  const { timeoutMs = DEFAULT_API_TIMEOUT_MS, signal: externalSignal, ...requestInit } = init;
  const headers = new Headers(init.headers ?? {});
  const isFormData = typeof FormData !== "undefined" && requestInit.body instanceof FormData;
  if (!isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!headers.has("X-Requested-With")) {
    headers.set("X-Requested-With", "XMLHttpRequest");
  }

  const controller = new AbortController();
  const abortFromExternal = () => {
    controller.abort(externalSignal?.reason ?? new DOMException("Request aborted", "AbortError"));
  };
  const timeoutId = globalThis.setTimeout(() => {
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);
  if (externalSignal) {
    if (externalSignal.aborted) {
      abortFromExternal();
    } else {
      externalSignal.addEventListener("abort", abortFromExternal, { once: true });
    }
  }

  let response: Response;
  try {
    response = await fetch(buildApiUrl(path), {
      ...requestInit,
      credentials: requestInit.credentials ?? (API_BASE ? "include" : "same-origin"),
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      const reason = controller.signal.reason;
      const isTimeout =
        reason instanceof DOMException
          ? reason.name === "TimeoutError"
          : String((reason as { name?: string } | undefined)?.name || "").toLowerCase() === "timeouterror";
      throw new Error(isTimeout ? `Request timed out after ${timeoutMs}ms` : "Request was cancelled");
    }
    throw error instanceof Error ? error : new Error("Network request failed");
  } finally {
    globalThis.clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }

  const raw = await response.text();
  let parsed: unknown = {};
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      parsed = raw;
    }
  }

  if (!response.ok) {
    const message =
      typeof parsed === "object" && parsed && "detail" in parsed
        ? String((parsed as { detail?: string }).detail)
        : typeof parsed === "object" && parsed && "message" in parsed
          ? String((parsed as { message?: string }).message)
          : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }

  return parsed as T;
}

export async function uploadVideo(file: File, token: string): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);
  const response = await apiFetch<UploadResponse>(
    "/api/upload/video",
    { method: "POST", body, timeoutMs: DEFAULT_UPLOAD_TIMEOUT_MS },
    token,
  );
  if (response.status !== "success" || !response.video_id) {
    throw new Error(response.message || "Video upload failed");
  }
  return response;
}
