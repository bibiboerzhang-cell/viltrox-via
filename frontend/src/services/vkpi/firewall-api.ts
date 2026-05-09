import { apiFetch, jsonBody } from "../http";

export interface VkpiFirewallControlStatus {
  feature_flags?: { flags: Array<{ flag_key: string; enabled: number | boolean; description?: string }> };
  platform_settings?: { platforms: Array<{ platform: string; crawl_enabled: number | boolean; monthly_budget_usd?: number; daily_account_limit?: number; last_test_status?: string }> };
  budget_settings?: { budgets: Array<{ budget_key: string; monthly_limit_usd: number; current_month_spent: number; alert_threshold_pct: number; enabled: number | boolean }> };
}

export async function getFirewallControlStatus(token: string): Promise<VkpiFirewallControlStatus> {
  return apiFetch<VkpiFirewallControlStatus>(
    "/api/admin/vkpi/settings/firewall/control-status",
    {},
    token,
  );
}

export async function updateFirewallFeatureFlag(token: string, flagKey: string, enabled: boolean) {
  return apiFetch<{ flag_key: string; enabled: boolean }>(
    "/api/admin/vkpi/settings/firewall/feature-flags",
    {
      method: "POST",
      body: jsonBody({ flag_key: flagKey, enabled }),
    },
    token,
  );
}

export async function updateFirewallPlatform(
  token: string,
  platform: string,
  payload: {
    crawl_enabled?: boolean | number;
    monthly_budget_usd?: number;
    daily_account_limit?: number;
    posts_per_account?: number;
  },
) {
  return apiFetch<{ platform: string }>(
    `/api/admin/vkpi/settings/firewall/platform/${encodeURIComponent(platform)}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}

export async function updateFirewallBudget(
  token: string,
  budgetKey: string,
  payload: {
    monthly_limit_usd?: number;
    alert_threshold_pct?: number;
    enabled?: boolean | number;
  },
) {
  return apiFetch<{ budget_key: string }>(
    `/api/admin/vkpi/settings/firewall/budget/${encodeURIComponent(budgetKey)}`,
    {
      method: "POST",
      body: jsonBody(payload),
    },
    token,
  );
}
