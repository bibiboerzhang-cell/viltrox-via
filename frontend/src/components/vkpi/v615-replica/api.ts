import { apiFetch, jsonBody, type AuthUser, type MeResponse } from "../../../services/http";

type Row = Record<string, unknown>;

async function settle<T>(request: Promise<T>, fallback: T): Promise<T> {
  try {
    return await request;
  } catch {
    return fallback;
  }
}

export async function fetchV615ShellBundle(apiToken: string) {
  const [me, alerts] = await Promise.all([
    settle(apiFetch<MeResponse>("/api/auth/me", { timeoutMs: 3000 }, apiToken), { status: "error" }),
    settle(apiFetch<{ alerts?: Row[]; items?: Row[]; count?: number }>("/api/admin/vkpi/alerts?status=open&limit=80", { timeoutMs: 3500 }, apiToken), { alerts: [] }),
  ]);

  return {
    user: me.status === "success" ? me.user : undefined,
    alerts: Array.isArray(alerts.alerts) ? alerts.alerts : Array.isArray(alerts.items) ? alerts.items : [],
  };
}

export async function fetchV615DashboardBundle(apiToken: string) {
  // 2026-06-12 波3 R8:revenue-trend(14 行全零)与 product-performance(rows=[])为死取数,
  // 前端解析后零消费,已从 90s 轮询 bundle 中移除;接真后再恢复。
  const [
    dashboard,
    distribution,
    recentContent,
    copilotBrief,
    tasks,
    marketCards,
    starredProjects,
    fitMovers,
  ] = await Promise.all([
    settle(apiFetch<Row>("/api/admin/vkpi/dashboard?window_days=30", { timeoutMs: 4000 }, apiToken), {}),
    settle(apiFetch<Row>("/api/admin/vkpi/dashboard/kol-distribution-pack?limit=250", { timeoutMs: 2500 }, apiToken), {}),
    settle(apiFetch<{ items?: Row[] }>("/api/admin/vkpi/dashboard/recent-content?limit=30", { timeoutMs: 4000 }, apiToken), { items: [] }),
    settle(apiFetch<Row>("/api/admin/vkpi/dashboard/copilot-brief", { timeoutMs: 2500 }, apiToken), {}),
    settle(apiFetch<Row>("/api/admin/vkpi/dashboard/tasks?limit=8", { timeoutMs: 2500 }, apiToken), {}),
    settle(apiFetch<Row>("/api/admin/vkpi/industry-data/market-intelligence/cards/v0?limit=120&brand_limit=5&include_latest_llm_artifact=false&include_latest_external_smoke=false", { timeoutMs: 2500 }, apiToken), {}),
    settle(apiFetch<{ projects?: Row[] }>("/api/admin/vkpi/projects?limit=100&starred=true", { timeoutMs: 3500 }, apiToken), { projects: [] }),
    settle(apiFetch<Row>("/api/admin/vkpi/dashboard/fit-movers?limit=8", { timeoutMs: 2500 }, apiToken), {}),
  ]);

  return {
    dashboard,
    distribution,
    recentContent: Array.isArray(recentContent.items) ? recentContent.items : [],
    copilotBrief,
    tasks,
    marketCards,
    starredProjects: Array.isArray(starredProjects.projects) ? starredProjects.projects : [],
    fitMovers,
  };
}

export async function resolveV615Alert(apiToken: string, alertId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/alerts/${encodeURIComponent(String(alertId))}/resolve`,
    { method: "POST", body: jsonBody({}) },
    apiToken,
  );
}

export async function submitV615Feedback(
  apiToken: string,
  payload: {
    category: string;
    title: string;
    description: string;
    screenshot?: { name: string; type: string; size: number; dataUrl: string } | null;
    metadata?: Row;
  },
) {
  const typeMap: Record<string, string> = {
    bug: "bug",
    feature: "suggestion",
    ux: "button_issue",
    other: "question",
  };
  const pagePath = typeof window !== "undefined"
    ? `${window.location.pathname}${window.location.search}${window.location.hash}`
    : "v615Replica";
  const screenshot = payload.screenshot
    ? {
        name: payload.screenshot.name,
        type: payload.screenshot.type,
        size: payload.screenshot.size,
        data_url: payload.screenshot.dataUrl,
      }
    : null;
  return apiFetch<Row>(
    "/api/admin/vkpi/feedback",
    {
      method: "POST",
      body: jsonBody({
        feedback_type: typeMap[payload.category] || "bug",
        severity: payload.category === "bug" ? "high" : "medium",
        title: payload.title,
        detail: payload.description,
        page_path: pagePath,
        metadata: {
          ...(payload.metadata || {}),
          raw_category: payload.category,
          source: "v615_feedback_modal",
          screenshot,
          user_agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
        },
      }),
    },
    apiToken,
  );
}

export async function logoutV615() {
  return apiFetch<Row>("/api/auth/logout", { method: "POST" });
}

export type { AuthUser, Row };
