// 法务页与公开 DSAR 表单的接口层(匿名,无 token)。
// GET /api/public/legal/policy 已登记进发布只读白名单(release_validation);POST 写口按设计在验收窗口内围栏。
import { ApiResponseError, apiFetch, jsonBody } from "../../lib/api";

export interface LegalPolicyBucket {
  bucket: string;
  policy_key: string;
  days: number;
  default_days: number;
  label_zh: string;
  label_en: string;
}

export interface LegalPolicy {
  status: string;
  draft: boolean;
  legal_review: string;
  version: string;
  contact_email: string;
  contact_email_configured: boolean;
  retention: LegalPolicyBucket[];
  purge_task_key: string;
  purge_gate_env: string;
  purge_enabled: boolean;
  dsar_sla_days: number;
  public_form_path: string;
  request_types: string[];
  platforms: string[];
}

export type DsarRequestType = "erasure" | "access" | "do_not_contact";

export interface DsarRequestPayload {
  request_type: DsarRequestType | "";
  platform: string;
  handle: string;
  profile_url: string;
  contact_email: string;
  message: string;
  consent_confirmed: boolean;
  captcha_token: string;
  /** 蜜罐:真人看不见;机器人填了后端直接拒。 */
  website: string;
}

export interface DsarRequestReceipt {
  status: string;
  public_ref: string;
  request_type: string;
  sla_days: number;
  suppression: { status: string } | null;
}

export function fetchLegalPolicy(): Promise<LegalPolicy> {
  return apiFetch<LegalPolicy>("/api/public/legal/policy");
}

export function submitDsarRequest(payload: DsarRequestPayload): Promise<DsarRequestReceipt> {
  return apiFetch<DsarRequestReceipt>("/api/public/dsar/requests", { method: "POST", body: jsonBody(payload) });
}

/** 后端 400 的稳定 code(detail.code);429 → rate_limited;其余 → ""(用消息兜底)。 */
export function dsarErrorCode(error: unknown): string {
  if (error instanceof ApiResponseError) {
    if (error.status === 429) return "rate_limited";
    const detail = error.detail;
    if (detail && typeof detail === "object" && typeof (detail as { code?: unknown }).code === "string") {
      return String((detail as { code: string }).code);
    }
    return "";
  }
  const detail = (error as { detail?: { code?: unknown } } | null)?.detail;
  return detail && typeof detail.code === "string" ? detail.code : "";
}
