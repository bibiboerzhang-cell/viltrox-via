import { apiFetch, jsonBody, type AuthUser, type MeResponse } from "../../../services/http";
// 车道A(2026-07-07):shell/dashboard 两个 GET bundle 走内存缓存层(45s TTL + 并发去重 +
// stale-while-revalidate)。cockpit 按 activeNav 条件渲染,切 tab=卸载重挂=此前全量重拉;
// 现在重挂命中缓存立即回上次数据,后台静默刷新。POST 写路径一律仍走 apiFetch,不缓存。
import { cachedApiFetch, clearApiCache } from "../../../lib/apiCache";

type Row = Record<string, unknown>;

async function settle<T>(request: Promise<T>, fallback: T): Promise<T> {
  try {
    return await request;
  } catch {
    return fallback;
  }
}

export async function fetchCockpitShellBundle(apiToken: string) {
  const [me, alerts] = await Promise.all([
    settle(cachedApiFetch<MeResponse>("/api/auth/me", { timeoutMs: 3000 }, apiToken), { status: "error" }),
    settle(cachedApiFetch<{ alerts?: Row[]; items?: Row[]; count?: number }>("/api/admin/vkpi/alerts?status=open&limit=80", { timeoutMs: 3500 }, apiToken), { alerts: [] }),
  ]);

  return {
    user: me.status === "success" ? me.user : undefined,
    alerts: Array.isArray(alerts.alerts) ? alerts.alerts : Array.isArray(alerts.items) ? alerts.items : [],
  };
}

export async function fetchCockpitDashboardBundle(apiToken: string) {
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
    aiTodayHot,
    competitorRadar,
  ] = await Promise.all([
    // 主 summary 是最重的一刀(KPI/漏斗/campaigns 全靠它):并发批里线上实测 ~4s+ 才完成,
    // 4000ms 会静默超时回空 → 整排 KPI + KOL 漏斗显示"待接入/待后端"。放宽到 10s(2026-07-02)。
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard?window_days=30", { timeoutMs: 10000 }, apiToken), {}),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/kol-distribution-pack?limit=250", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<{ items?: Row[] }>("/api/admin/vkpi/dashboard/recent-content?limit=30", { timeoutMs: 4000 }, apiToken), { items: [] }),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/copilot-brief", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/tasks?limit=8", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/industry-data/market-intelligence/cards/v0?limit=120&brand_limit=5&include_latest_llm_artifact=false&include_latest_external_smoke=false", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<{ projects?: Row[] }>("/api/admin/vkpi/projects?limit=100&starred=true", { timeoutMs: 3500 }, apiToken), { projects: [] }),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/fit-movers?limit=8", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/ai-today-hot", { timeoutMs: 2500 }, apiToken), {}),
    settle(cachedApiFetch<Row>("/api/admin/vkpi/dashboard/competitor-radar", { timeoutMs: 2500 }, apiToken), {}),
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
    aiTodayHot,
    competitorRadar,
  };
}

// 报告深度分析:把「生成报告」拼好的全量真实数据 POST 给后端,LLM 整理成经营分析(预算闸 + 当天缓存)。
export async function fetchCockpitReportAnalysis(
  apiToken: string,
  reportText: string,
  period: string,
  language: string,
): Promise<Row> {
  return apiFetch<Row>(
    "/api/admin/vkpi/dashboard/report-analysis",
    { method: "POST", body: jsonBody({ report_text: reportText, period, language }), timeoutMs: 120000 },
    apiToken,
  );
}

export async function resolveCockpitAlert(apiToken: string, alertId: string | number) {
  return apiFetch<Row>(
    `/api/admin/vkpi/alerts/${encodeURIComponent(String(alertId))}/resolve`,
    { method: "POST", body: jsonBody({}) },
    apiToken,
  );
}

export async function submitCockpitFeedback(
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
    : "cockpit";
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
          source: "cockpit_feedback_modal",
          screenshot,
          user_agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
        },
      }),
    },
    apiToken,
  );
}

export async function logoutCockpit() {
  // 车道A:登出即清 GET 内存缓存,防止切身份后读到上一个账号的数据。
  clearApiCache();
  return apiFetch<Row>("/api/auth/logout", { method: "POST" });
}

export type { AuthUser, Row };
