import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// MY KOL 内容墙模块冒烟(contentWall · 2026-07-12)。
// 主冒烟文件(MyKolBoardPage.smoke.test.tsx)对 recent_videos 恒喂诚实 empty ——
// 墙卡上的 KOL 名/三档徽与库行同名,塞真行会把主文件既有单匹配断言全打成多匹配;
// 网格真身/仅V/排序/增页/单 KOL 视图/组失败诚实降级的行为冒烟全住本文件
// (预置布局只挂 contentWall,零无关模块零串台)。
// mock seam 同主文件:services/http.apiFetch(全页唯一网络出口)按 path 路由,零真实 HTTP。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { MyKolBoardPage } from "./MyKolBoardPage";

// 库行(KOL 下拉选项来源;形状对齐 my_kol_aggregate._pool_favorites,最小字段)
const FAVS = [
  { kol_pool_id: 101, display_name: "Alpha Cam", handle: "@alpha", platform: "youtube", followers: 120000, viltrox_fit_score: 82, avatar_url: "", profile_url: "", country: "US", is_shared: false, shared_by_name: "", created_at: "2026-06-01T00:00:00Z", projects: [], contacts: [] },
  { kol_pool_id: 102, display_name: "Beta Vlog", handle: "@beta", platform: "instagram", followers: 5600, viltrox_fit_score: null, avatar_url: "", profile_url: "", country: "", is_shared: true, shared_by_name: "Alice", created_at: "2026-06-02T00:00:00Z", projects: [], contacts: [] },
];

const AGG = {
  staff: { id: 3, name: "Boss", email: "boss@viltrox.com" },
  window_days: 30,
  pool_favorites: FAVS,
  projects: [],
  claims: [],
  kpi_summary: { favorites_count: 2, claimed_count: 0, in_project_count: 0, published_count: 0, projects_count: 0 },
};

const MATRIX = { platforms: [], account_count: 0, post_count: 0, total_views: 0, staff_managed: [] };

// 14 条墙条目:1 合作(已深析 + 真缩略图)+ 1 标题提及(view NULL=未实测)+ 12 未判定填充
// → 默认恰好溢出一页(12),增页/排序/仅V 全可判。
const filler = (i: number) => ({
  evidence_id: 9200 + i, kol_pool_id: 103, project_id: null, content_url: `https://example.com/v${i}`,
  platform: "tiktok", title: `Filler clip ${i}`, video_title: "", view_count: 100 + i, like_count: i,
  publish_date: `2026-06-${String(28 - i).padStart(2, "0")}`, kol_name: "Gamma Films", kol_handle: "@gamma",
  has_final_v1_cache: false, thumbnail_url: null, cached_thumbnail_url: null, youtube_thumbnail_url: null, best_thumbnail: null,
});
const WALL_ITEMS = [
  { evidence_id: 9101, kol_pool_id: 101, project_id: 7, content_url: "https://youtu.be/w1", platform: "youtube", title: "Wall Coop Film", video_title: "", view_count: 8000, like_count: 40, publish_date: "2026-07-11", kol_name: "Alpha Cam", kol_handle: "@alpha", has_final_v1_cache: true, thumbnail_url: null, cached_thumbnail_url: null, youtube_thumbnail_url: "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg", best_thumbnail: "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg" },
  { evidence_id: 9102, kol_pool_id: 102, project_id: null, content_url: "https://instagram.example/w2", platform: "instagram", title: "VILTROX wall mention", video_title: "", view_count: null, like_count: 7, publish_date: "2026-07-10", kol_name: "Beta Vlog", kol_handle: "@beta", has_final_v1_cache: false, thumbnail_url: null, cached_thumbnail_url: null, youtube_thumbnail_url: null, best_thumbnail: null },
  ...Array.from({ length: 12 }, (_, i) => filler(i + 1)),
];

const EXT = {
  status: "ready",
  days: 30,
  recent_videos: {
    status: "ready", limit: 60, items: WALL_ITEMS,
    page: { limit: 60, returned: WALL_ITEMS.length, has_more: false, next_cursor: null, cursor_kind: "published_at_id", order: "published_at_desc_id_desc" },
    basis: "vkpi_kol_video_evidence 收藏集最近采集(封顶 60)",
  },
};

// 单 KOL 视图(/kol-pool/{id}/videos 同源端点):Alpha 两条 / Beta 零条
const KOL_VIDEOS: Record<string, unknown[]> = {
  "101": [
    { evidence_id: 9001, id: 9001, kol_pool_id: 101, project_id: 7, title: "On set with the new lens", view_count: 1000, like_count: 10, has_final_v1_cache: true, publish_date: "2026-07-01", content_url: "https://youtu.be/a1" },
    { evidence_id: 9003, id: 9003, kol_pool_id: 101, project_id: null, title: "daily vlog", view_count: 500, like_count: 3, has_final_v1_cache: false, publish_date: "2026-06-10", content_url: "https://youtu.be/a3" },
  ],
  "102": [],
};

function recentGroup(items: unknown[], cursor: string | null = null, nextCursor: string | null = null) {
  return {
    status: items.length ? "ready" : "empty",
    limit: 60,
    items,
    page: {
      limit: 60, returned: items.length, has_more: Boolean(nextCursor), next_cursor: nextCursor,
      cursor_kind: "published_at_id", order: "published_at_desc_id_desc",
    },
    filters: { days: 0, kol_pool_id: null },
    cursor,
  };
}

function routeApi(overrides: {
  boardExt?: unknown;
  recentPage?: (url: URL) => unknown;
} = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/my-kol/aggregate")) return AGG;
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext/recent-videos")) {
      const url = new URL(p, "https://test.local");
      const poolId = url.searchParams.get("kol_pool_id") || "";
      const days = Number(url.searchParams.get("days") || 0);
      const since = url.searchParams.get("since") || (days ? "2026-08-17T12:00:00+00:00" : null);
      const result = overrides.recentPage ? await overrides.recentPage(url) : null;
      if (result && typeof result === "object") {
        return {
          ...result,
          filters: { days, kol_pool_id: Number(poolId) || null, since },
        };
      }
      const rows = poolId ? (KOL_VIDEOS[poolId] || []).map((row) => ({
        ...row,
        tasks: {
          metric_refresh: TRACKED.has(Number(row.evidence_id))
            ? { status: "queued", job_id: 77, requested_at: "2026-08-21T10:00:00Z", data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } }
            : { status: "not_requested", job_id: null, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
          final_v1: { status: "not_requested", job_id: null, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
        },
      })) : WALL_ITEMS;
      return {
        ...recentGroup(rows),
        filters: { days, kol_pool_id: Number(poolId) || null, since },
      };
    }
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext")) {
      const value = overrides.boardExt ?? EXT;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/marketing/channels/official-matrix")) return MATRIX;
    const dataWatchMatch = p.match(/\/api\/admin\/vkpi\/my-kol\/(\d+)\/videos\/(\d+)\/data-watch$/);
    if (dataWatchMatch && (init as RequestInit | undefined)?.method === "POST") {
      const body = JSON.parse(String((init as RequestInit).body || "{}"));
      DATA_WATCH_CALLS.push({ poolId: Number(dataWatchMatch[1]), evidenceId: Number(dataWatchMatch[2]), body });
      if (DATA_WATCH_HANDLER) return DATA_WATCH_HANDLER({ poolId: Number(dataWatchMatch[1]), evidenceId: Number(dataWatchMatch[2]), body });
      if (DATA_WATCH_REQUIRE_SKU && !Array.isArray(body.product_skus)) {
        return {
          status: "sku_required",
          candidates: [
            { sku_code: "AF-85-F14", sku_name: "AF 85mm F1.4 Pro", match_source: "final_v1_lens_evidence_v2", modalities: ["visual"] },
            { sku_code: "AF-35-F18", sku_name: "AF 35mm F1.8", match_source: "catalog" },
          ],
        };
      }
      if (DATA_WATCH_DETECTED_PENDING && !Array.isArray(body.product_skus)) {
        return {
          status: "tracking", evidence_id: Number(dataWatchMatch[2]), skus: ["AF-85-F14"], sku_source: "auto",
          sku_provenance: {
            relation_type: "detected", source: "final_v1_lens_evidence_v2", confidence: 0.85,
            requires_human_confirmation: true, modalities: ["visual"], evidence_excerpt: "AF 85 in frame",
          },
          tracking: "active", refresh: "queued",
        };
      }
      return {
        status: "tracking", evidence_id: Number(dataWatchMatch[2]),
        skus: body.product_skus || ["AF-AUTO"], sku_source: body.product_skus ? "manual" : "auto",
        tracking: "active", refresh: "queued",
      };
    }
    const trackMatch = p.match(/\/api\/admin\/vkpi\/my-kol\/(\d+)\/videos$/);
    if (trackMatch && (init as RequestInit | undefined)?.method === "POST") {
      TRACK_CALLS.push({ poolId: Number(trackMatch[1]), body: JSON.parse(String((init as RequestInit).body)) });
      if (Number(trackMatch[1]) === 102) throw Object.assign(new Error("my_kol_paid_action_write_forbidden"), { detail: "my_kol_paid_action_write_forbidden", status: 403 });
      TRACKED.add(Number(TRACK_CALLS[TRACK_CALLS.length - 1].body.content_url === "https://youtu.be/a1" ? 9001 : 0));
      return { status: "queued", evidence_id: 9001, job_id: 77 };
    }
    const videosMatch = p.match(/\/api\/admin\/vkpi\/(?:kol-pool|my-kol)\/(\d+)\/videos(?:\?|$)/);
    if (videosMatch) {
      const items = (KOL_VIDEOS[videosMatch[1]] || []).map((row) => ({
        ...row,
        tasks: {
          metric_refresh: TRACKED.has(Number(row.evidence_id))
            ? { status: "queued", job_id: 77, requested_at: "2026-08-21T10:00:00Z", data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } }
            : { status: "not_requested", job_id: null, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
          final_v1: { status: "not_requested", job_id: null, data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false } },
        },
      }));
      return {
        contract: "my_kol_video_recovery_v1", kol_pool_id: Number(videosMatch[1]), read_only: true, items,
        summary: { total: items.length, views_total: 0, views_measured: 0, final_v1_ready: 0 },
        page: { limit: 24, returned: items.length, has_more: false, next_cursor: null, cursor_kind: "published_at_id", order: "published_at_desc_id_desc" },
      };
    }
    if (p.startsWith("/api/admin/vkpi/my-kol/watch-overview")) {
      return { contract: "my_kol_watchlist_overview_v1", groups: [], totals: { kol_count: 0, group_count: 0 }, scheduler: { enabled: false }, empty_reason: "no_groups" };
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}
const TRACK_CALLS: Array<{ poolId: number; body: { content_url: string; product_skus: string[] } }> = [];
const TRACKED = new Set<number>();
const DATA_WATCH_CALLS: Array<{ poolId: number; evidenceId: number; body: Record<string, unknown> }> = [];
let DATA_WATCH_REQUIRE_SKU = false;
let DATA_WATCH_DETECTED_PENDING = false;
let DATA_WATCH_HANDLER: ((call: { poolId: number; evidenceId: number; body: Record<string, unknown> }) => unknown | Promise<unknown>) | null = null;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

// 预置布局只挂内容墙(布局键 v2);matrix hook 有按 token 分键的模块级缓存 → 固定独立 token
const renderWall = () => {
  window.localStorage.setItem("vkpi-my-kol-layout-v2", JSON.stringify([{ moduleKey: "contentWall", span: 8 }]));
  return render(<MyKolBoardPage apiToken="wall-token" viewMode="manager" data={{ projects: [], staffMembers: [], kolOptions: [] } as any} />);
};

const wallCards = () => [...document.querySelectorAll("[title='点卡直跳原帖']")];
const wallTitles = () => wallCards().map((el) => (el.querySelector("div.truncate") as HTMLElement)?.textContent);

beforeEach(() => {
  window.localStorage.clear();
  TRACK_CALLS.length = 0;
  TRACKED.clear();
  DATA_WATCH_CALLS.length = 0;
  DATA_WATCH_REQUIRE_SKU = false;
  DATA_WATCH_DETECTED_PENDING = false;
  DATA_WATCH_HANDLER = null;
  routeApi();
});

describe("MyKolBoardPage 内容墙(contentWall:收藏集最近采集视频网格)", () => {
  it("网格真身:卡=缩略图链/标题/KOL 名/播放点赞 mono(NULL=未实测)/三档徽/已深析标;默认 12 张 + 查看更多增页", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    // 卡头短计数 = 后端条数真值;工具行三件套在场
    expect(screen.getAllByText("近 14 条").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("按 KOL 筛选")).toHaveClass("min-h-9", "text-[12px]");
    expect(screen.getByRole("button", { name: "品牌相关" })).toHaveClass("min-h-9", "text-[11.5px]");
    // 默认按最新排序:第 1 张=07-11 合作片,第 2 张=07-10 标题提及;默认恰 12 张
    expect(wallTitles().slice(0, 2)).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    expect(wallCards().length).toBe(12);
    // 点卡=原帖链接(新开页);缩略图=best_thumbnail 真图,其余三路皆无 → 诚实 ▶ 占位
    expect(wallCards()[0].getAttribute("href")).toBe("https://youtu.be/w1");
    expect(wallCards()[0].getAttribute("target")).toBe("_blank");
    expect(wallCards()[0].querySelector("img")?.getAttribute("src")).toBe(
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent("https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg")}`,
    );
    expect(wallCards()[1].querySelector("img")).toBeNull();
    // 播放读数诚实:NULL → 未实测(≠ 0;regex 会撞 SrcChip 口径行,收敛到卡本体)
    expect(wallCards()[1].textContent).toContain("▶ 未实测");
    expect(wallCards()[0].textContent).toContain("▶ 8,000");
    expect(screen.getByText("合作产出")).toBeTruthy();
    expect(screen.getByText("标题提及 V")).toBeTruthy();
    expect(screen.getAllByText("待深析").length).toBeGreaterThanOrEqual(10);
    expect(screen.getAllByText("已深析").length).toBe(1);
    // 增页:已显 12 / 14 → 点后全量 14,按钮消失
    const more = screen.getByText(/展开更多卡片/);
    expect(more.textContent).toContain("已显 12 / 已查询 14");
    fireEvent.click(more);
    await waitFor(() => {
      expect(wallCards().length).toBe(14);
      expect(screen.queryByText(/展开更多卡片/)).toBeNull();
    });
  });

  it("同源代理返回 1x1 失败占位时显示诚实 ▶，不把透明 SVG 当真缩略图", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    const image = wallCards()[0].querySelector("img") as HTMLImageElement;
    expect(image).toBeTruthy();
    Object.defineProperty(image, "naturalWidth", { configurable: true, value: 1 });
    Object.defineProperty(image, "naturalHeight", { configurable: true, value: 1 });
    fireEvent.load(image);
    expect(wallCards()[0].querySelector("img")).toBeNull();
    expect(wallCards()[0].querySelector("[title='缩略图加载失败(不摆假图)']")).toBeTruthy();
  });

  it("品牌相关 + 播放排序:未判定隐藏;实测播放降序、未实测排最后(不当 0 混序)", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.click(screen.getByText("品牌相关"));
    expect(wallTitles()).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    expect(screen.queryByText("Filler clip 1")).toBeNull();
    expect(screen.getAllByText("待深析")).toHaveLength(1); // 筛选按钮仍在，未判定卡已隐藏
    // 排序切「播放」(仍仅 V):8000 实测在前,NULL 未实测排最后
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(wallTitles()).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    // 关掉仅 V + 播放排序:填充片(100+i)按实测降序,未实测仍最后一张
    fireEvent.click(screen.getByText("全部已采集"));
    fireEvent.click(screen.getByText(/展开更多卡片/));
    await waitFor(() => expect(wallCards().length).toBe(14));
    const titles = wallTitles();
    expect(titles[0]).toBe("Wall Coop Film");
    expect(titles[1]).toBe("Filler clip 12");
    expect(titles[titles.length - 1]).toBe("VILTROX wall mention");
  });

  it("深析结构化品牌证据三态进入内容墙:present/absent/unknown 分别落画面口播识别 V / 深析未见 V / 待深析,并可分开筛查", async () => {
    const analyzedItems = [
      { ...filler(31), evidence_id: 9301, title: "portrait setup", llm_viltrox_status: "present", llm_viltrox_detected: true, llm_viltrox_products: ["AF 85mm F1.4 Pro"], has_final_v1_cache: true },
      { ...filler(32), evidence_id: 9302, title: "street diary", llm_viltrox_status: "absent", llm_viltrox_detected: false, has_final_v1_cache: true },
      { ...filler(33), evidence_id: 9303, title: "camera walk", llm_viltrox_detected: null, has_final_v1_cache: false },
      // 深析过但未完整检查(unknown):旧布尔 false 也不能当「不相关」,必须落待深析(员工反馈 #4)
      { ...filler(34), evidence_id: 9304, title: "night market", llm_viltrox_status: "unknown", llm_viltrox_detected: false, has_final_v1_cache: true },
    ];
    routeApi({ boardExt: { ...EXT, recent_videos: { status: "ready", limit: 60, items: analyzedItems } } });
    renderWall();

    expect(await screen.findByText("画面/口播识别 V")).toBeTruthy();
    // 角标与筛选 chip 同名(「深析未见 V」各一),卡片角标带口径 tooltip
    const unseenBadge = screen.getAllByText("深析未见 V").find((node) => node.tagName === "SPAN");
    expect(unseenBadge).toHaveAttribute("title", expect.stringContaining("没有见到 Viltrox"));
    const pendingBadges = screen.getAllByText("待深析").filter((node) => node.tagName === "SPAN");
    expect(pendingBadges).toHaveLength(2);
    expect(pendingBadges[0]).toHaveAttribute("title", expect.stringContaining("不等于不相关"));
    fireEvent.click(screen.getByRole("button", { name: "深析未见 V" }));
    expect(wallTitles()).toEqual(["street diary"]);
    fireEvent.click(screen.getByRole("button", { name: "待深析" }));
    expect(wallTitles()).toEqual(["night market", "camera walk"]);
    fireEvent.click(screen.getByRole("button", { name: "全部已采集" }));
    expect(wallCards()).toHaveLength(4);
  });

  it("单 KOL 视图:选中收藏 KOL 走 recent-videos 同源组合筛选;零采集 KOL 诚实空;切回全部零重取", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByText("On set with the new lens")).toBeTruthy();
    expect(screen.getByText("daily vlog")).toBeTruthy();
    expect(screen.queryByText("Wall Coop Film")).toBeNull();
    const calls = () => apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calls().some((p) => p.includes("/my-kol/board-ext/recent-videos?") && p.includes("kol_pool_id=101"))).toBe(true);
    // 零采集 KOL → 板面空态口径(带 KOL 名,不透传后端字段)
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "102" } });
    expect(await screen.findByText(/Beta Vlog 暂无已采集内容——可在KOL详情发起补采。/)).toBeTruthy();
    // 切回全部:回 board-ext 首页(已在手,不再发请求)
    const before = calls().length;
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "0" } });
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    expect(calls().length).toBe(before);
  });

  it("全量查询沿服务端 keyset 翻过首 60 条到真末页", async () => {
    const all = Array.from({ length: 65 }, (_, index) => {
      const publishedAt = new Date(Date.UTC(2026, 7, 24) - index * 3_600_000).toISOString();
      return {
        ...filler(index + 100), evidence_id: 10000 + index, title: `All video ${index + 1}`,
        publish_date: publishedAt.slice(0, 10), published_at: publishedAt,
      };
    });
    const first = recentGroup(all.slice(0, 60), null, "page-2");
    routeApi({
      boardExt: { ...EXT, recent_videos: first },
      recentPage: (url) => url.searchParams.get("cursor") === "page-2"
        ? recentGroup(all.slice(60), "page-2", null)
        : first,
    });
    renderWall();
    expect(await screen.findByText("All video 1")).toBeTruthy();
    expect(screen.getAllByText(/已查询 60\+/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /查询当前范围全量/ }));
    await waitFor(() => expect(screen.getByText(/已查询 65｜/)).toBeTruthy());
    expect(apiFetchMock.mock.calls.map((call) => String(call[0])).some(
      (path) => path.includes("/board-ext/recent-videos?") && path.includes("cursor=page-2"),
    )).toBe(true);
    expect(screen.queryByRole("button", { name: /查询当前范围全量/ })).toBeNull();
  });

  it("全部/单 KOL 与全部/7/15/30 天八种组合均可达,参数在服务端下推", async () => {
    routeApi({ recentPage: (url) => {
      const days = Number(url.searchParams.get("days") || 0);
      const poolId = Number(url.searchParams.get("kol_pool_id") || 0);
      return recentGroup([{
        ...filler(80 + days), evidence_id: 10800 + days + poolId, kol_pool_id: poolId || 101,
        title: poolId ? `KOL ${poolId} d${days}` : `ALL d${days}`,
      }]);
    } });
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    for (const day of [7, 15, 30] as const) {
      fireEvent.click(screen.getByRole("button", { name: `滚动近 ${day} 天` }));
      expect(await screen.findByText(`ALL d${day}`)).toBeTruthy();
    }
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByText("KOL 101 d30")).toBeTruthy();
    for (const day of [15, 7] as const) {
      fireEvent.click(screen.getByRole("button", { name: `滚动近 ${day} 天` }));
      expect(await screen.findByText(`KOL 101 d${day}`)).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "全部时间" }));
    expect(await screen.findByText("KOL 101 d0")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "0" } });
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    const calls = apiFetchMock.mock.calls.map((call) => String(call[0]));
    for (const day of [7, 15, 30]) {
      expect(calls.some((path) => path.includes(`recent-videos?days=${day}`) && !path.includes("kol_pool_id"))).toBe(true);
      expect(calls.some((path) => path.includes(`recent-videos?days=${day}`) && path.includes("kol_pool_id=101"))).toBe(true);
    }
    expect(calls.some((path) => path.includes("recent-videos?days=0") && path.includes("kol_pool_id=101"))).toBe(true);
    expect(screen.getByRole("button", { name: "全部时间" })).toHaveAttribute("aria-pressed", "true");
  });

  it("切换有限时间窗后等新首页返回，再自动沿游标查到真末页", async () => {
    const rows = Array.from({ length: 65 }, (_, index) => ({
      ...filler(index + 300), evidence_id: 13000 + index, title: `D7 video ${index + 1}`,
    }));
    routeApi({
      boardExt: {
        ...EXT,
        recent_videos: { ...recentGroup(WALL_ITEMS, null, "old-page-2"), filters: { days: 0, kol_pool_id: null } },
      },
      recentPage: (url) => url.searchParams.get("cursor") === "d7-page-2"
        ? recentGroup(rows.slice(60), "d7-page-2", null)
        : recentGroup(rows.slice(0, 60), null, "d7-page-2"),
    });
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "滚动近 7 天" }));
    await waitFor(() => expect(screen.getByText(/已查询 65｜/)).toBeTruthy());
    expect(apiFetchMock.mock.calls.map((call) => String(call[0])).some(
      (path) => path.includes("days=7") && path.includes("cursor=d7-page-2"),
    )).toBe(true);
    const secondPageUrl = apiFetchMock.mock.calls.map((call) => String(call[0])).find(
      (path) => path.includes("cursor=d7-page-2"),
    );
    expect(new URL(String(secondPageUrl), "https://test.local").searchParams.get("since")).toBe("2026-08-17T12:00:00+00:00");
  });

  it("动作后从新首页游标重走已加载深度，首部新增视频不造成边界丢项", async () => {
    const oldRows = Array.from({ length: 65 }, (_, index) => {
      const publishedAt = new Date(Date.UTC(2026, 7, 24) - index * 3_600_000).toISOString();
      return {
        ...filler(index + 400), evidence_id: 14000 + index, title: `Refresh video ${index + 1}`,
        publish_date: publishedAt.slice(0, 10), published_at: publishedAt,
      };
    });
    const newest = {
      ...filler(499), evidence_id: 14999, title: "Refresh newest",
      publish_date: "2026-08-24", published_at: "2026-08-24T01:00:00.000Z",
    };
    let refreshed = false;
    routeApi({
      boardExt: { ...EXT, recent_videos: recentGroup(oldRows.slice(0, 60), null, "old-p2") },
      recentPage: (url) => {
        const cursor = url.searchParams.get("cursor");
        if (!refreshed) return cursor === "old-p2" ? recentGroup(oldRows.slice(60), "old-p2", null) : recentGroup(oldRows.slice(0, 60), null, "old-p2");
        if (cursor === "new-p2") return recentGroup(oldRows.slice(59), "new-p2", null);
        if (cursor === "old-p2") return recentGroup(oldRows.slice(60), "old-p2", null);
        return recentGroup([newest, ...oldRows.slice(0, 59)], null, "new-p2");
      },
    });
    renderWall();
    expect(await screen.findByText("Refresh video 1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /查询当前范围全量/ }));
    await waitFor(() => expect(screen.getByText(/已查询 65｜/)).toBeTruthy());
    refreshed = true;
    const firstCard = screen.getByText("Refresh video 1").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(firstCard).getByRole("button", { name: "追踪播放" }));
    await waitFor(() => expect(screen.getByText(/已查询 66｜/)).toBeTruthy());
    expect(apiFetchMock.mock.calls.map((call) => String(call[0])).some(
      (path) => path.includes("cursor=new-p2"),
    )).toBe(true);
  });

  it("首次查询失败可重试;下一页/全量中断保留已读结果并可继续", async () => {
    let firstFails = true;
    routeApi({ recentPage: (url) => {
      if (url.searchParams.get("kol_pool_id") === "101" && firstFails) throw new Error("first page unavailable");
      return recentGroup(KOL_VIDEOS["101"] || []);
    } });
    const initialRender = renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("first page unavailable");
    firstFails = false;
    fireEvent.click(screen.getByRole("button", { name: "重新查询" }));
    expect(await screen.findByText("On set with the new lens")).toBeTruthy();
    initialRender.unmount();
    window.localStorage.clear();

    const all = Array.from({ length: 67 }, (_, index) => {
      const publishedAt = new Date(Date.UTC(2026, 7, 24) - index * 3_600_000).toISOString();
      return {
        ...filler(index + 200), evidence_id: 12000 + index, title: `Recoverable ${index + 1}`,
        publish_date: publishedAt.slice(0, 10), published_at: publishedAt,
      };
    });
    const first = recentGroup(all.slice(0, 60), null, "p2");
    let mode: "next-fail" | "mid-fail" | "ok" = "next-fail";
    routeApi({
      boardExt: { ...EXT, recent_videos: first },
      recentPage: (url) => {
        const cursor = url.searchParams.get("cursor");
        if (cursor === "p2" && mode === "next-fail") return { status: "error", reason: "next page unavailable", items: [] };
        if (cursor === "p2") return recentGroup(all.slice(60, 65), "p2", "p3");
        if (cursor === "p3" && mode === "mid-fail") throw new Error("mid query unavailable");
        return recentGroup(all.slice(65), "p3", null);
      },
    });
    renderWall();
    expect(await screen.findByText("Recoverable 1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /查询下一批/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/已保留 60 条/);
    expect(screen.getByText("Recoverable 1")).toBeTruthy();
    mode = "mid-fail";
    fireEvent.click(screen.getByRole("button", { name: "继续全量查询" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/已保留 65 条/));
    mode = "ok";
    fireEvent.click(screen.getByRole("button", { name: "继续全量查询" }));
    await waitFor(() => expect(screen.getByText(/已查询 67｜/)).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sku_required 在墙内显式选 SKU 后二次提交,不默认猜产品", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    DATA_WATCH_REQUIRE_SKU = true;
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    const card = screen.getByText("Wall Coop Film").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "数据关注" }));
    const picker = await screen.findByRole("group", { name: "为数据关注选择 SKU" });
    await waitFor(() => expect(scrollIntoView.mock.instances).toContain(picker));
    expect(within(picker).getByText(/第 2 步/)).toBeTruthy();
    expect(within(picker).getByText(/确认成功后会自动打开对应 SKU/)).toBeTruthy();
    expect(within(picker).getAllByRole("checkbox")[0]).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(/第 1 步已完成.*完成第 2 步.*单品播放数据/);
    expect(DATA_WATCH_CALLS).toHaveLength(1);
    expect(DATA_WATCH_CALLS[0].body).toEqual({});
    expect(screen.getByRole("button", { name: "确认关联并关注" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: /AF-85-F14/ }));
    fireEvent.click(screen.getByRole("button", { name: /确认关联并关注/ }));
    await waitFor(() => expect(DATA_WATCH_CALLS).toHaveLength(2));
    expect(DATA_WATCH_CALLS[1]).toMatchObject({ poolId: 101, evidenceId: 9101, body: { product_skus: ["AF-85-F14"] } });
    expect(await screen.findByText(/已登记数据关注\(SKU AF-85-F14\)/)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "为数据关注选择 SKU" })).toBeNull();
  });

  it("detected 待确认留在墙内选择器，不提前跳转或冒充抓取完成", async () => {
    DATA_WATCH_DETECTED_PENDING = true;
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    const card = screen.getByText("Wall Coop Film").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "数据关注" }));

    const picker = await screen.findByRole("group", { name: "为数据关注选择 SKU" });
    expect(picker).toBeTruthy();
    expect(screen.getByText(/尚未登记为员工确认的单品关注/)).toBeTruthy();
    expect(screen.getByText(/排队.*不代表抓取完成/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认系统识别并关注" })).toBeDisabled();
    expect(DATA_WATCH_CALLS).toHaveLength(1);
  });

  it("A 慢成功、B 快 sku_required 反序返回时，旧 A 不抢 B 的 picker/receipt/跳转", async () => {
    const slowA = deferred<unknown>();
    const changed = vi.fn();
    window.addEventListener("vkpi:sku-play-changed", changed);
    DATA_WATCH_HANDLER = ({ evidenceId }) => evidenceId === 9101 ? slowA.promise : {
      status: "sku_required", candidates: [{ sku_code: "AF-35-F18", sku_name: "AF 35mm" }],
    };
    renderWall();
    const cardA = (await screen.findByText("Wall Coop Film")).closest("[data-vkpi-wall-card]") as HTMLElement;
    const cardB = screen.getByText("Filler clip 1").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(cardA).getByRole("button", { name: "数据关注" }));
    fireEvent.click(within(cardB).getByRole("button", { name: "数据关注" }));

    const picker = await screen.findByRole("group", { name: "为数据关注选择 SKU" });
    expect(picker).toHaveAttribute("data-vkpi-data-watch-sku-picker", "9201");
    await act(async () => {
      slowA.resolve({
        status: "tracking", skus: ["AF-85-F14"], refresh: "queued",
        sku_provenance: { relation_type: "manual", requires_human_confirmation: false },
      });
      await slowA.promise;
    });
    expect(screen.getByRole("group", { name: "为数据关注选择 SKU" })).toHaveAttribute("data-vkpi-data-watch-sku-picker", "9201");
    expect(screen.getByText(/第 1 步已完成.*完成第 2 步.*单品播放数据/)).toBeTruthy();
    expect(changed).not.toHaveBeenCalled();
    await waitFor(() => expect(within(cardA).getByRole("button", { name: "数据关注" })).toBeEnabled());
    window.removeEventListener("vkpi:sku-play-changed", changed);
  });

  it("A 慢 detected、B 快成功反序返回时，只保留 B 成功与单品跳转", async () => {
    const slowA = deferred<unknown>();
    const changed = vi.fn();
    window.addEventListener("vkpi:sku-play-changed", changed);
    DATA_WATCH_HANDLER = ({ evidenceId }) => evidenceId === 9101 ? slowA.promise : {
      status: "tracking", skus: ["AF-35-F18"], refresh: "already_queued",
      sku_provenance: { relation_type: "manual", requires_human_confirmation: false },
    };
    renderWall();
    const cardA = (await screen.findByText("Wall Coop Film")).closest("[data-vkpi-wall-card]") as HTMLElement;
    const cardB = screen.getByText("Filler clip 1").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(cardA).getByRole("button", { name: "数据关注" }));
    fireEvent.click(within(cardB).getByRole("button", { name: "数据关注" }));
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({ evidenceId: 9201, skus: ["AF-35-F18"] });
    expect(await screen.findByText(/已登记数据关注\(SKU AF-35-F18\).*已在队列中/)).toBeTruthy();

    await act(async () => {
      slowA.resolve({
        status: "tracking", skus: ["AF-85-F14"], refresh: "queued",
        sku_provenance: { relation_type: "detected", source: "title_alias_v1", requires_human_confirmation: true },
      });
      await slowA.promise;
    });
    expect(changed).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("group", { name: "为数据关注选择 SKU" })).toBeNull();
    expect(screen.getByText(/已登记数据关注\(SKU AF-35-F18\).*已在队列中/)).toBeTruthy();
    await waitFor(() => expect(within(cardA).getByRole("button", { name: "数据关注" })).toBeEnabled());
    window.removeEventListener("vkpi:sku-play-changed", changed);
  });

  it("已有 A picker 后发起 B auto，会立即撤下旧 picker；B 未返回前不可误提交 A", async () => {
    const slowB = deferred<unknown>();
    DATA_WATCH_HANDLER = ({ evidenceId }) => evidenceId === 9101
      ? { status: "sku_required", candidates: [{ sku_code: "AF-85-F14" }] }
      : slowB.promise;
    renderWall();
    const cardA = (await screen.findByText("Wall Coop Film")).closest("[data-vkpi-wall-card]") as HTMLElement;
    const cardB = screen.getByText("Filler clip 1").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(cardA).getByRole("button", { name: "数据关注" }));
    expect(await screen.findByRole("group", { name: "为数据关注选择 SKU" })).toHaveAttribute("data-vkpi-data-watch-sku-picker", "9101");
    fireEvent.click(within(cardB).getByRole("button", { name: "数据关注" }));
    expect(screen.queryByRole("group", { name: "为数据关注选择 SKU" })).toBeNull();
    await act(async () => {
      slowB.resolve({ status: "sku_required", candidates: [{ sku_code: "AF-35-F18" }] });
      await slowB.promise;
    });
    expect(await screen.findByRole("group", { name: "为数据关注选择 SKU" })).toHaveAttribute("data-vkpi-data-watch-sku-picker", "9201");
  });

  it("员工反馈 #5:卡片「追踪播放」入口 + 引导文案;入队后单 KOL 视图按服务端任务态显示「播放追踪排队中」;共享只读如实回执", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    // 引导一句在工具行下方,员工第一眼可见;聚合视图无逐条任务态 → 诚实不摆假「未发起」
    expect(screen.getByText("想追踪某条视频的播放?")).toBeTruthy();
    expect(screen.queryByText(/播放追踪未发起/)).toBeNull();
    // 聚合视图也能追踪(行带 kol_pool_id);入队只报排队,不冒充完成
    const trackButtons = screen.getAllByRole("button", { name: "追踪播放" });
    expect(trackButtons.length).toBeGreaterThan(0);
    fireEvent.click(trackButtons[0]);
    await waitFor(() => expect(TRACK_CALLS).toHaveLength(1));
    expect(TRACK_CALLS[0]).toEqual({ poolId: 101, body: { content_url: "https://youtu.be/w1", product_skus: [] } });
    expect(await screen.findByRole("status")).toHaveTextContent(/已登记追踪并排队抓取/);

    // 切到单 KOL:契约行带 tasks → 两层状态;再点追踪 → refresh 后 chip 读服务端「排队中」并禁用按钮
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByText("On set with the new lens")).toBeTruthy();
    expect(screen.getAllByText("播放追踪未发起").length).toBeGreaterThan(0);
    const card = screen.getByText("On set with the new lens").closest("[data-vkpi-wall-card]") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: "追踪播放" }));
    await waitFor(() => expect(TRACK_CALLS).toHaveLength(2));
    expect(await within(card).findByText("播放追踪排队中")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "追踪进行中" })).toBeDisabled();
    expect(card.querySelector("[data-vkpi-task-status='queued']")).not.toBeNull();
    expect(screen.getByText(/每 2 秒同步一次状态/)).toBeTruthy();
  });

  it("组失败/组空诚实降级:error → 该组聚合失败卡(带 reason);empty → 板面空态文案", async () => {
    routeApi({ boardExt: { ...EXT, recent_videos: { status: "error", reason: "recent_videos exploded" } } });
    renderWall();
    expect(await screen.findByText("该组聚合失败")).toBeTruthy();
    expect(screen.getByText(/recent_videos exploded/)).toBeTruthy();
    window.localStorage.clear();
    routeApi({ boardExt: { ...EXT, recent_videos: { status: "empty", reason: "收藏集内零 evidence——内容墙诚实空,不摆假卡。" } } });
    renderWall();
    expect(await screen.findByText("暂无已采集内容——可在KOL详情发起补采。")).toBeTruthy();
  });
});
