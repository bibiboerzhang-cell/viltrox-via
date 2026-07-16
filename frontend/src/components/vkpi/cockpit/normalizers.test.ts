import { describe, it, expect } from "vitest";
import {
  normalizeAlerts,
  normalizeKolFunnel,
  latestCalendarDate,
  eventCoords,
  normalizeTopMovers,
  normalizeDashboardSourceHealth,
  normalizeAiInsight,
  normalizeSignals,
  normalizeCockpitDashboard,
} from "./normalizers";

// 源 normalizers.ts 的 `items = []` / `kolRows = []` 默认参被 TS 推断为 never[]
// (源文件无类型注解,且不在本 lane 可改范围)。测试侧用 row 数组喂入是预期用法,
// 这里把行数据声明为 Row[] 再经 Parameters<> 桥接到形参类型,既不放宽源签名也无 any。
type Row = Record<string, unknown>;
const asCalendarItems = (rows: Row[]): Parameters<typeof latestCalendarDate>[0] =>
  rows as unknown as Parameters<typeof latestCalendarDate>[0];
const asMoverRows = (rows: Row[]): Parameters<typeof normalizeTopMovers>[0] =>
  rows as unknown as Parameters<typeof normalizeTopMovers>[0];

// P2-6: cockpit normalizers 纯归一化逻辑(无网络)。本测试已在严格 tsc 下零报错。
// 选纯且无随机精确值的导出;凡涉及 Date.now()/jitter 只断言「字段存在/类型/排序/长度」。
// 红线:normalizeTopMovers 只留真分,绝不出现非真占位行。

describe("normalizeAlerts 分类/严重度/分桶", () => {
  it("feedback 识别 → category=feedback + iconColor amber", () => {
    const out = normalizeAlerts([{ target_type: "team_feedback", title: "反馈" }]);
    const alert = out.all[0];
    expect(alert.category).toBe("feedback");
    expect(alert.iconColor).toBe("#f59e0b");
    expect(alert.source).toBe("vkpi_feedback");
  });

  it("severity critical → 收敛为 high,iconColor 红", () => {
    const out = normalizeAlerts([{ severity: "critical", title: "崩" }]);
    expect(out.all[0].severity).toBe("high");
    expect(out.all[0].iconColor).toBe("#ef4444");
  });

  it("resolved_at → status=done,unread=false", () => {
    const out = normalizeAlerts([{ title: "done", resolved_at: "2026-06-01" }]);
    expect(out.all[0].status).toBe("done");
    expect(out.all[0].unread).toBe(false);
  });

  it("未 resolved → unread=true,status=todo", () => {
    const out = normalizeAlerts([{ title: "open" }]);
    expect(out.all[0].unread).toBe(true);
    expect(out.all[0].status).toBe("todo");
  });

  it("notifications 桶排除 reminder/task;reminders 桶只含它们", () => {
    const out = normalizeAlerts([
      { title: "通知", category: "notification" },
      { title: "提醒事项", category: "reminder" },
      { title: "任务", category: "task" },
    ]);
    expect(out.notifications.map((a) => a.title)).toEqual(["通知"]);
    expect(out.reminders.map((a) => a.title).sort()).toEqual(["任务", "提醒事项"]);
  });

  it("空输入 → 三桶皆空数组", () => {
    const out = normalizeAlerts([]);
    expect(out.all).toEqual([]);
    expect(out.notifications).toEqual([]);
    expect(out.reminders).toEqual([]);
  });
});

describe("normalizeKolFunnel 真伪判定", () => {
  it("全 null → isReal=false,4 个阶段 count=null", () => {
    const out = normalizeKolFunnel({});
    expect(out.isReal).toBe(false);
    expect(out.stages).toHaveLength(4);
    expect(out.stages.every((s: { count: number | null }) => s.count == null)).toBe(true);
  });

  it("任一 count 非空 → isReal=true", () => {
    const out = normalizeKolFunnel({ funnel: { favorites_total: 5 } });
    expect(out.isReal).toBe(true);
    expect(out.stages[0].count).toBe(5);
  });

  it("by_staff 归一化为对象数组", () => {
    const out = normalizeKolFunnel({ funnel: { claimed_total: 1, by_staff: [{ staff_id: 9 }] } });
    expect(out.byStaff).toHaveLength(1);
    expect(out.byStaff[0]).toMatchObject({ staff_id: 9 });
  });
});

describe("latestCalendarDate 取最近日期", () => {
  it("混合日期串取最大 YYYY-MM-DD", () => {
    const out = latestCalendarDate(asCalendarItems([
      { posted_at: "2026-05-28" },
      { published_at: "2026-06-10" },
      { created_at: "2026-04-01" },
    ]));
    expect(out).toBe("2026-06-10");
  });

  it("空输入 → null", () => {
    expect(latestCalendarDate([])).toBeNull();
  });
});

describe("eventCoords 落点逻辑", () => {
  it("显式 lat/lng 直接返回", () => {
    expect(eventCoords("US", 12.5, -34.2)).toEqual({ lat: 12.5, lng: -34.2 });
  });

  it("无经纬 + 无可识别国家 → null", () => {
    expect(eventCoords("", null, null)).toBeNull();
    expect(eventCoords("ZZ", null, null)).toBeNull();
  });

  it("只有国家不得伪造精确点", () => {
    expect(eventCoords("US", null, null, "seed")).toBeNull();
  });
});

describe("normalizeTopMovers 真分排序(红线)", () => {
  it("只留 v6_fit != null,DESC 排序,≤5", () => {
    const out = normalizeTopMovers(asMoverRows([
      { id: 1, handle: "@a", v6_fit: 70, followers: 100 },
      { id: 2, handle: "@b", v6_fit: 95, followers: 200 },
      { id: 3, handle: "@c", v6_fit: null, followers: 999 },
      { id: 4, handle: "@d", v6_fit: 82 },
    ]));
    expect(out).toHaveLength(3);
    expect(out.map((m: { raw: { v6_fit: number } }) => m.raw.v6_fit)).toEqual([95, 82, 70]);
  });

  it("无 v6_fit 的行被剔除(不出现非真占位)", () => {
    const out = normalizeTopMovers(asMoverRows([
      { id: 1, handle: "@noscore", followers: 5000 },
      { id: 2, handle: "@onlyfit", v6_fit: 88 },
    ]));
    expect(out).toHaveLength(1);
    expect(out[0].handle).toBe("@onlyfit");
    // deltaFollower 必须是真分,绝不出现「待评估」占位
    expect(out[0].deltaFollower).toBe("Fit 88");
  });

  it("超过 5 条只取前 5", () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({ id: i, handle: `@k${i}`, v6_fit: 10 + i }));
    expect(normalizeTopMovers(asMoverRows(rows))).toHaveLength(5);
  });

  it("空输入 → 空数组", () => {
    expect(normalizeTopMovers([])).toEqual([]);
  });
});

describe("normalizeDashboardSourceHealth 数据源透明度", () => {
  it("区分接口在线、接口异常与尚未接入的指标能力", () => {
    const out = normalizeDashboardSourceHealth({
      _sources: {
        dashboard: { ok: true, label: "增长总览" },
        distribution: { ok: false, label: "KOL 地图" },
      },
    });

    expect(out).toMatchObject({ available: true, total: 2, ready: 1, degraded: true });
    expect(out.failed).toEqual(["KOL 地图"]);
    expect(out.pendingCapabilities).toEqual(["GMV", "ROI"]);
    expect(out.label).toBe("1/2 可用 · 2 待接");
  });

  it("旧缓存没有状态字段时保持兼容", () => {
    expect(normalizeDashboardSourceHealth({})).toMatchObject({ available: false, label: "实时" });
  });
});

describe("Dashboard 公司账号真实指标契约", () => {
  it("把官方账号 roster、30d 曝光和互动率送入四张公司卡及详情", () => {
    const out = normalizeCockpitDashboard({
      dashboard: {
        summary: {
          active_roster: 525,
          official_account_count: 18,
          evidence_metrics: {
            active_roster_by_scope: { all: 525, kol: 484, company: 18 },
            active_30d_by_scope: { all: 114, kol: 95, company: 18, owned: 18, window_days: 30 },
            total_exposure: 2_052_179_053,
            engagement: { total_engagement: 35_059_369, total_views: 2_052_179_053, engagement_rate: 0.01708397 },
            coverage: { evidence_total: 100, view_covered: 90, view_coverage_pct: 0.9 },
            roster_detail: {
              active_roster: 525,
              total_pool: 1225,
              company: { total_pool: 18, active_roster: 18, followers: 1_240_484, total_views: 370_719_506 },
            },
          },
          active_30d_by_scope: { all: null, kol: null, owned: null },
          exposure_30d_by_scope: { all: null, kol: null, owned: 27_806_378, company: 27_806_378 },
          engagement_rate_by_scope: { all: null, kol: null, owned: 3.9723855, company: 3.9723855 },
          metric_series_by_scope: {
            company: {
              "kol-count": {
                points: [
                  { date: "2026-07-09", value: 16 },
                  { date: "2026-07-10", value: 18 },
                ],
                delta_pct: 12.5,
                window_days: 30,
                basis: "official_account_snapshots",
                coverage: { observed_days: 2 },
              },
            },
            owned: {
              "kol-count": { points: [1, 2], delta_pct: 100 },
              "active-30d": { points: [15, 17, 18], delta_pct: 20 },
              exposure: {
                points: [24_100_000, 26_800_000, 27_806_378],
                delta_pct: 15.3792,
                basis: { source: "owned_channel_metrics" },
                coverage: 0.93,
              },
              engagement: {
                points: [3.41, 3.67, 3.9723855],
                delta_pct: 16.4922,
              },
            },
          },
        },
      },
      _sources: { dashboard: { ok: true, label: "增长总览" } },
    }, []);

    const metric = (id: string) => out.metrics.find((item: { id: string }) => item.id === id);
    expect(metric("kol-count")?.data.company.value).toBe(18);
    expect(metric("active-30d")?.data.company.value).toBe(18);
    expect(metric("exposure")?.data.company.value).toBe(27_806_378);
    expect(metric("engagement")?.data.company.value).toBeCloseTo(3.9723855);
    expect(metric("kol-count")?.data.company).toMatchObject({
      spark: [16, 18],
      deltaPct: 12.5,
      windowDays: 30,
      basis: "official_account_snapshots",
      coverage: { observed_days: 2 },
    });
    expect(metric("active-30d")?.data.company.spark).toEqual([15, 17, 18]);
    expect(metric("exposure")?.data.company).toMatchObject({
      spark: [24_100_000, 26_800_000, 27_806_378],
      deltaPct: 15.3792,
      basis: { source: "owned_channel_metrics" },
      coverage: 0.93,
    });
    expect(metric("engagement")?.data.company.spark).toEqual([3.41, 3.67, 3.9723855]);
    expect(metric("kol-count")?.rosterDetail.company).toMatchObject({
      total_pool: 18,
      active_roster: 18,
      followers: 1_240_484,
      total_views: 370_719_506,
    });
  });
});

describe("Dashboard KOL 地图分布来源优先级", () => {
  it("global distribution-pack 与 Pool 行重叠时也不重复计数", () => {
    const out = normalizeCockpitDashboard({
      distribution: {
        resource: "dashboard.kol_distribution_pack",
        schema_version: 1,
        is_real: true,
        scope: { mode: "global" },
        stats: { mapped_kol_count: 3 },
        countries: [{ code: "US", lat: 39.8, lng: -98.6, count: 3, cities: [] }],
      },
    }, [
      { id: 1, country: "US", city: "NYC" },
      { id: 2, country: "DE", city: "Berlin" },
    ]);

    expect(out.mapHierarchy.US.count).toBe(3);
    expect(out.mapHierarchy.DE).toBeUndefined();
  });

  it("distribution-pack 缺失时保留 kolRows fallback", () => {
    const out = normalizeCockpitDashboard({ distribution: {} }, [
      { id: 1, country: "US", city: "NYC" },
    ]);

    expect(out.mapHierarchy.US.count).toBe(1);
  });
});

describe("AI Today 真实证据与新鲜度", () => {
  it("保留视频、来源与过期状态", () => {
    const out = normalizeAiInsight({}, {}, {
      available: true,
      freshness_status: "stale",
      content: {
        headline: "先复核再投放",
        generated_at: "2026-07-01T00:00:00Z",
        freshness_status: "stale",
        freshness_label: "已过期 · 8 天前",
        snapshot_date: "2026-07-01",
        shooting_plans: ["弱光街拍"],
        hot_topics: ["cinematic"],
        product_recommendations: ["EVO · 适配轻量街拍"],
        content_recommendations: ["YouTube · 做镜头对比"],
        video_recommendations: ["用外部样例解释构图"],
        recommended_videos: [{ evidence_id: 7, content_url: "https://example.com/video" }],
        sources: [{ url: "https://example.com/source", title: "source" }],
      },
    });

    expect(out.isStale).toBe(true);
    expect(out.updatedLabel).toBe("已过期 · 8 天前");
    expect(out.recommendedVideos).toHaveLength(1);
    expect(out.productRecommendations).toEqual(["EVO · 适配轻量街拍"]);
    expect(out.contentRecommendations).toEqual(["YouTube · 做镜头对比"]);
    expect(out.videoRecommendations).toEqual(["用外部样例解释构图"]);
    expect(out.sources).toHaveLength(1);
    expect(out.todayDecision.reason).toContain("过期快照");
  });

  it("保留可用快照并单独映射最新失败尝试", () => {
    const out = normalizeAiInsight({}, {}, {
      available: true,
      latest_attempt: {
        attempted_at: "2026-07-16T12:05:00Z",
        status: "invalid",
        provider: "anthropic",
        model: "claude-sonnet-4-5",
        reason: "invalid_result_contract",
        provider_status: "transient_error",
        generation_status: "all_providers_failed",
        providers_attempted: ["google", "anthropic"],
      },
      content: {
        headline: "保留的可用快照",
        generated_at: "2026-07-16T11:00:00Z",
        freshness_status: "fresh",
        shooting_plans: ["计划"],
        hot_topics: ["话题"],
        sources: [{ url: "https://example.com/source" }],
      },
    });

    expect(out.todayDecision.text).toBe("保留的可用快照");
    expect(out.latestAttempt).toMatchObject({
      status: "invalid",
      provider: "anthropic",
      reason: "invalid_result_contract",
      generationStatus: "all_providers_failed",
      providersAttempted: ["google", "anthropic"],
    });
  });
});

describe("竞品雷达来源归一化", () => {
  it("原始 URL、数据表 ID 与过期状态进入详情", () => {
    const [signal] = normalizeSignals({}, {
      available: true,
      freshness_status: "stale",
      content: {
        freshness_status: "stale",
        freshness_label: "已过期 · 8 天前",
        snapshot_date: "2026-07-01",
        items: [{
          brand: "Sony",
          title: "FX 动态",
          summary: "真实摘要",
          impact: "威胁现有人像主题",
          sources: [{
            title: "Reddit source",
            url: "https://reddit.com/r/example",
            provider: "reddit",
            relation_type: "brand_context",
            ledger_table: "vkpi_market_mentions",
            ledger_id: 12,
          }],
        }],
      },
    });

    expect(signal.stale).toBe(true);
    expect(signal.sources[0]).toMatchObject({
      url: "https://reddit.com/r/example",
      sourceTable: "vkpi_market_mentions",
      sourceId: 12,
    });
    expect(signal.sourceLine).toContain("1 条关联证据");
    expect(signal.sourceLine).toContain("原始引文未保留");
    expect(signal.impact[0].text).toContain("威胁");
  });
});
