// G1 外联信号 API:「敢给差评」信号 + 外联三承诺文案读端。
// - critic-signal:词表法纯聚合已有数据(创作者标题/描述 + 深析转述),零 LLM,不进任何评分;
// - three-promises:三承诺中英双语模板常量 + 开关态(打行业三大怨气:拖款/借测逼还/黑名单文化)。
// 红线:纯读展示;不渲染任何 viltrox/v6_fit 数值;草稿仅供人审后手动外发。
// 沿用 kolOutreach-api.ts 的 apiFetch<T>(path, init, token) 调用风格(独立小文件)。
import { apiFetch } from "../http";

export type VkpiCriticExample = {
  source?: string; // deep_analysis | creator_title | creator_description
  evidence_id?: number | null;
  title?: string;
  content_url?: string;
  view_count?: number | null;
  quote?: string;
  matched?: string[];
};

export type VkpiCriticSignal = {
  status?: string; // ready | error
  reason?: string;
  kol_pool_id?: number;
  kol?: { handle?: string; display_name?: string; platform?: string };
  has_critic_history?: boolean;
  example?: VkpiCriticExample | null;
  basis?: string;
  examples?: VkpiCriticExample[];
  counts?: { deep_analysis?: number; creator_title?: number; creator_description?: number };
  coverage?: { evidence_count?: number; deep_analyzed_count?: number; with_description?: number };
  method?: string;
  generated_at?: string;
  note?: string;
};

export type VkpiThreePromises = {
  version?: string;
  enabled?: boolean;
  env_flag?: string;
  items?: { key?: string; zh?: string; en?: string; pain_point_zh?: string }[];
  block_en?: string;
  block_zh?: string;
  note?: string;
};

// GET /api/admin/vkpi/kol-pool/{id}/critic-signal  (require_tab vkpi:read)
// 「敢给差评」信号:该 KOL 历史上公开批评过产品 = 可信度加分(词表法,零 LLM/零写库)。
export async function getKolCriticSignal(token: string, kolPoolId: string | number) {
  return apiFetch<VkpiCriticSignal>(
    `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/critic-signal`,
    { cache: "no-store" },
    token,
  );
}

// GET /api/admin/vkpi/outreach/three-promises  (require_tab vkpi:read)
// 外联三承诺文案 + 开关态(纯常量读端)。
export async function getOutreachThreePromises(token: string) {
  return apiFetch<VkpiThreePromises>(
    "/api/admin/vkpi/outreach/three-promises",
    { cache: "no-store" },
    token,
  );
}
