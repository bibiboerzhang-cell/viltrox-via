import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// 车道 L4:数值跟进 / 镜头出镜 两模块 + 详情「用过的镜头」真身断言(端点 mock,零真网络)。

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { LensExposureEmbed, MetricTrackingEmbed } from "./MyKolBoardPage.embeds";
import { KolLensUsageList } from "./MyKolBoardPage.charts";
import {
  dailyAvgText,
  nextRefreshText,
  trendStateBadge,
  windowDeltaText,
  type VkpiMetricTrendsResponse,
} from "../../../../services/vkpi/myKolBoard-api";
import { lensViewsText, modalityChips, type LensInsightsSummary } from "../../../../services/vkpi/lensInsights-api";

const TRENDS_OK: VkpiMetricTrendsResponse = {
  contract: "my_kol_metric_trends_v1",
  read_only: true,
  scope: { mode: "team_collection", staff_scope_id: null },
  scheduler: { task_key: "vkpi_kol_video_metric_refresh", enabled: false, interval_hours: 1, cadence_hours: { hot: 6, warm: 24, cold: 168 }, failed_backoff_hours: 24 },
  summary: { tracked_total: 2, active: 2, paused: 0, measured: 1, with_history: 1, views_latest_total: 1500, windows: { "7d": { views: { delta: 800, videos: 1 } } } },
  items: [
    {
      evidence_id: 41,
      kol_pool_id: 9,
      kol_name: "Creator Nine",
      title: "Hot portrait video",
      content_url: "https://www.youtube.com/watch?v=abc",
      tracking: { status: "active", history: "ready", next_refresh: { tier: "hot", estimated_at: null, reason: "scheduler_disabled" } },
      latest: { fetched_at: "2026-08-22T09:00:00+00:00", status: "success", views: 1500, likes: 100, comments: 9 },
      last_attempt: { fetched_at: "2026-08-22T09:00:00+00:00", status: "success", views: 1500 },
      sample_count: 3,
      failed_count: 0,
      attempt_count: 3,
      windows: {
        "7d": { views: { delta: 800, daily_avg: 101.2, status: "ready", covered_days: 7.9 }, likes: { delta: 40, status: "ready" }, comments: { delta: 4, status: "ready" } },
        "30d": { views: { delta: 1400, daily_avg: 45.1, status: "partial", covered_days: 31 }, likes: { delta: 90, status: "ready" }, comments: { delta: 8, status: "ready" } },
      },
      series: [
        { fetched_at: "2026-08-14T09:00:00+00:00", status: "success", views: 700, likes: 60, comments: 5 },
        { fetched_at: "2026-08-20T09:00:00+00:00", status: "success", views: 1200, likes: 90, comments: 8 },
        { fetched_at: "2026-08-22T09:00:00+00:00", status: "success", views: 1500, likes: 100, comments: 9 },
      ],
    },
    {
      evidence_id: 42,
      kol_pool_id: 9,
      kol_name: "Creator Nine",
      title: "Never measured clip",
      tracking: { status: "active", history: "never_measured", next_refresh: { tier: "cold", estimated_at: null, reason: "scheduler_disabled" } },
      latest: null,
      last_attempt: { fetched_at: "2026-08-22T01:00:00+00:00", status: "failed", error_code: "runtimeerror" },
      sample_count: 0,
      failed_count: 1,
      attempt_count: 1,
      windows: { "7d": { views: { delta: null, status: "insufficient_history" } }, "30d": { views: { delta: null, status: "insufficient_history" } } },
      series: [],
    },
  ],
  truncated: false,
  empty_reason: null,
};

const LENS_OK: LensInsightsSummary = {
  contract: "lens_insights_v1",
  scope: { mode: "team_collection", staff_scope_id: null },
  coverage: { analysed_videos: 570, scanned_videos: 570, videos_with_products: 397, unscanned_videos: 0 },
  summary: { lenses: 2, videos_with_products: 396, kols_with_products: 203, mention_rows: 519, unresolved_rows: 21, modalities: { visual: 352, text: 124, voice: 154, unspecified: 136 } },
  modality_labels: { visual: "画面", text: "字幕·文字", voice: "口播", unspecified: "未注明" },
  lenses: [
    { lens_key: "af85mmf14pro", display_name: "AF 85mm F1.4 Pro", resolution: "sku", skus: ["AF-85MM-F14-PRO-FE", "AF-85MM-F14-PRO-Z"], videos: 66, kols: 57, views_total: 1459923, views_measured_videos: 60, modalities: { visual: 62, text: 28, voice: 24, unspecified: 17 }, samples: [{ evidence_id: 1, kol_name: "Jay", title: "85 Pro portrait", content_url: "https://www.youtube.com/watch?v=85", view_count: 1200, modalities: ["visual"] }] },
    { lens_key: "af35mmf18evo", display_name: "AF 35mm F1.8 EVO", resolution: "family", skus: [], videos: 27, kols: 26, views_total: null, views_measured_videos: 0, modalities: { visual: 13, unspecified: 11 }, samples: [] },
  ],
  lenses_truncated: false,
  unresolved: [{ mention: "35mm", videos: 7, kols: 7, candidate_skus: [] }],
  empty_reason: null,
};

function install(routes: Record<string, unknown | (() => unknown)>) {
  apiFetchMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    for (const [prefix, value] of Object.entries(routes)) {
      if (p.startsWith(prefix)) {
        const resolved = typeof value === "function" ? (value as () => unknown)() : value;
        if (resolved instanceof Error) throw resolved;
        return resolved;
      }
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe("数值跟进模块", () => {
  it("未追踪任何视频 → 诚实引导去详情登记,不摆假图", async () => {
    install({ "/api/admin/vkpi/my-kol/metrics/tracking-overview": { items: [], summary: { tracked_total: 0 }, scheduler: { enabled: false }, empty_reason: "no_tracked_videos" } });
    render(<MetricTrackingEmbed apiToken="t" noToken={<div>no token</div>} isManager />);
    expect(await screen.findByText("还没有被追踪的视频")).toBeTruthy();
    expect(screen.queryByText("最近播放")).toBeNull();
  });

  it("有追踪视频 → 实测曲线 + 增量表 + 调度未开启告示;点行切换曲线对象", async () => {
    install({ "/api/admin/vkpi/my-kol/metrics/tracking-overview": TRENDS_OK });
    render(<MetricTrackingEmbed apiToken="t" noToken={<div>no token</div>} isManager />);
    // 标题出现两处:曲线对象标头 + 表行
    expect((await screen.findAllByText("Hot portrait video")).length).toBe(2);
    expect(screen.getByText("2 条追踪")).toBeTruthy();
    expect(screen.getAllByText("自动刷新未开启").length).toBeGreaterThan(0);
    // 7d ready → +800;30d partial → 标「仅 31 天」;日均 +101/天
    // +800 出现两处:头部 7 天合计 + 表行
    expect(screen.getAllByText("+800").length).toBe(2);
    expect(screen.getByText("+1,400(仅 31 天)")).toBeTruthy();
    expect(screen.getByText("+101/天")).toBeTruthy();
    // 未实测行:状态徽 + 「未实测」而非 0
    expect(screen.getByText("最近刷新失败")).toBeTruthy();
    expect(screen.getAllByText("未实测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("待积累").length).toBeGreaterThan(0);
    // 曲线说明:3 次实测
    expect(screen.getByText(/播放 · 3 次实测/)).toBeTruthy();
    // 点第二行 → 曲线对象切换为无快照视频 → 曲线诚实空
    fireEvent.click(screen.getByText("Never measured clip"));
    expect(await screen.findByText(/尚无播放实测快照/)).toBeTruthy();
    // 切到点赞 tab 不报错(点表行切回有曲线的视频)
    fireEvent.click(screen.getByRole("button", { name: /Hot portrait video/ }));
    fireEvent.click(screen.getByRole("button", { name: "点赞" }));
    expect(await screen.findByText(/点赞 · 3 次实测/)).toBeTruthy();
  });

  it("端点失败 → 错误卡(不装空态)", async () => {
    install({ "/api/admin/vkpi/my-kol/metrics/tracking-overview": () => Object.assign(new Error("boom"), { detail: "no staff identity in scope" }) });
    render(<MetricTrackingEmbed apiToken="t" noToken={<div>no token</div>} isManager={false} />);
    expect(await screen.findByText("数值跟进读取失败")).toBeTruthy();
    expect(screen.getByText("no staff identity in scope")).toBeTruthy();
  });

  it("文案助手:增量 / 日均 / 下次刷新 / 状态徽口径", () => {
    expect(windowDeltaText({ delta: 12, status: "ready" })).toBe("+12");
    expect(windowDeltaText({ delta: -3, status: "partial", covered_days: 2.4 })).toBe("-3(仅 2 天)");
    expect(windowDeltaText({ delta: 5, status: "insufficient_history" })).toBe("待积累");
    expect(dailyAvgText({ daily_avg: null })).toBe("—");
    expect(dailyAvgText({ daily_avg: 12.6 })).toBe("+13/天");
    expect(nextRefreshText({ tracking: { status: "paused", pause_reason: "actor_revoked" } }, (ts) => ts)).toBe("已暂停 · actor_revoked");
    expect(nextRefreshText({ tracking: { status: "active", next_refresh: { reason: "scheduler_disabled" } } }, (ts) => ts)).toBe("自动刷新未开启");
    expect(nextRefreshText({ tracking: { status: "active", next_refresh: { reason: "estimated_by_cadence", estimated_at: "X" } } }, (ts) => `@${ts}`)).toBe("预计 @X 后");
    expect(trendStateBadge({ latest: null, last_attempt: { status: "failed" } }).label).toBe("最近刷新失败");
    expect(trendStateBadge({ latest: { views: 1 }, tracking: { history: "single_sample" } }).label).toBe("仅一次实测");
    expect(trendStateBadge({ latest: { views: 1 }, tracking: { history: "ready" } }).tone).toBe("good");
  });
});

describe("镜头出镜模块", () => {
  it("按镜头条形 + 证据来源 chips;点行展开样例;未对上目录原文保留", async () => {
    install({ "/api/admin/vkpi/lens-insights/summary": LENS_OK });
    render(<LensExposureEmbed apiToken="t" noToken={<div>no token</div>} isManager />);
    expect(await screen.findByText("AF 85mm F1.4 Pro")).toBeTruthy();
    expect(screen.getByText("2 款")).toBeTruthy();
    expect(screen.getByText("66 条 · 57 KOL")).toBeTruthy();
    expect(screen.getByText("27 条 · 26 KOL")).toBeTruthy();
    expect(screen.getByText(/未对上目录的提法/)).toBeTruthy();
    expect(screen.getByText(/35mm ×7/)).toBeTruthy();
    fireEvent.click(screen.getByText("AF 85mm F1.4 Pro"));
    expect(await screen.findByText("85 Pro portrait")).toBeTruthy();
    expect(screen.getByText("播放 1,459,923 · 6 条未实测")).toBeTruthy();
    expect(screen.getByText("型号已确认")).toBeTruthy();
    expect(screen.getByText("画面 62")).toBeTruthy();
    // 管理层才有「全部已深析」范围钮
    expect(screen.getByRole("button", { name: "全部已深析" })).toBeTruthy();
  });

  it("零深析 → 引导发起深析;已深析但未整理 → 说明回填未跑;员工无范围钮", async () => {
    install({ "/api/admin/vkpi/lens-insights/summary": { lenses: [], unresolved: [], coverage: { analysed_videos: 0, scanned_videos: 0, videos_with_products: 0, unscanned_videos: 0 }, summary: { lenses: 0 }, empty_reason: "no_lens_evidence" } });
    const first = render(<LensExposureEmbed apiToken="t" noToken={<div>no token</div>} isManager={false} />);
    expect(await screen.findByText("还没有已深析的视频")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "全部已深析" })).toBeNull();
    first.unmount();
    install({ "/api/admin/vkpi/lens-insights/summary": { lenses: [], unresolved: [], coverage: { analysed_videos: 12, scanned_videos: 0, videos_with_products: 0, unscanned_videos: 12 }, summary: { lenses: 0 }, empty_reason: "no_lens_evidence" } });
    render(<LensExposureEmbed apiToken="t" noToken={<div>no token</div>} isManager={false} />);
    expect(await screen.findByText("深析结果尚未整理成镜头清单")).toBeTruthy();
  });

  it("文案助手:播放未实测不当 0;modality chips 零计数不摆", () => {
    expect(lensViewsText({ views_total: null, views_measured_videos: 0, videos: 3 })).toBe("播放未实测");
    expect(lensViewsText({ views_total: 10, views_measured_videos: 1, videos: 3 })).toBe("播放 10 · 2 条未实测");
    expect(modalityChips({ visual: 2, text: 0, voice: 1 }).map((c) => c.label)).toEqual(["画面", "口播"]);
  });

  it("详情「用过的镜头」列表:镜头名 + 条数 + 证据 chips + 未对上原文", () => {
    render(<KolLensUsageList lenses={LENS_OK.lenses || []} unresolved={[{ mention: "Z2", videos: 1 }]} labels={LENS_OK.modality_labels} />);
    expect(screen.getByText("AF 35mm F1.8 EVO")).toBeTruthy();
    expect(screen.getByText("66 条")).toBeTruthy();
    expect(screen.getByText("系列已确认")).toBeTruthy();
    expect(screen.getByText(/还有 1 个产品提法未对上目录/)).toBeTruthy();
  });
});
