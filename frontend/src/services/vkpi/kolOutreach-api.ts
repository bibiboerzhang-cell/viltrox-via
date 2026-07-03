// C4 外联包 API(一键 brief + 中英双语邮件草稿 + 邮箱补抓状态)。
// 红线:后端零触 viltrox_fit_score;草稿仅供人审后手动外发,绝不自动发送。
// 沿用 kolPool-api.ts 的 apiFetch<T>(path, init, token) 调用风格(独立小文件,不动 dashboard-api.ts)。
import { apiFetch, jsonBody } from "../http";

export type VkpiOutreachEmailStatus = {
  state?: string; // present | missing
  email?: string;
  enrich?: { attempted?: boolean; status?: string; reason?: string };
};

export type VkpiOutreachPack = {
  schema_version?: string;
  kol?: { kol_pool_id?: number; handle?: string; display_name?: string; platform?: string };
  brief?: {
    product_focus?: string;
    recommended_product_lines?: string[];
    creator_type?: string;
    content_highlights?: string;
    audience_signal?: string;
    fit_verdict?: string;
    why_fit?: string[];
    watchouts?: string[];
    past_brand_collabs?: string[];
    sources?: { content_fit_v1?: boolean; viltrox_fit_reason?: boolean };
  };
  email_draft?: {
    subject?: string;
    email_en?: string;
    email_zh?: string;
    talking_points?: string[];
    personalized?: boolean;
  };
  provenance?: {
    provider?: string;
    model?: string;
    llm_used?: boolean;
    reason?: string;
    generated_at?: string;
    generated_date?: string;
  };
  note?: string;
};

export type VkpiOutreachPackResponse = {
  state?: string; // ready | missing | error
  kol_pool_id?: number;
  pack?: VkpiOutreachPack;
  model?: string;
  updated_at?: string;
  email?: VkpiOutreachEmailStatus;
  cached?: boolean;
  reason?: string;
};

// GET /api/admin/vkpi/kol-pool/{id}/outreach-pack  (require_tab vkpi:read)
// 读最新外联包缓存 + 实时邮箱状态;零 LLM/零外调。
export async function getKolOutreachPack(token: string, kolPoolId: string | number) {
  return apiFetch<VkpiOutreachPackResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/outreach-pack`,
    { cache: "no-store" },
    token,
  );
}

// POST /api/admin/vkpi/kol-pool/{id}/outreach-pack  (require_tab vkpi:write)
// 一键生成:brief 复用已有 why-fit/内容契合产物;邮件草稿走 llm_gateway(预算闸内置,
// 失败回落双语模板);邮箱缺失自动复用既有富化管线补抓。同 KOL 当日幂等,force=true 重生成。
export async function generateKolOutreachPack(token: string, kolPoolId: string | number, force = false) {
  return apiFetch<VkpiOutreachPackResponse>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/outreach-pack`,
    { method: "POST", body: jsonBody({ force }) },
    token,
  );
}
