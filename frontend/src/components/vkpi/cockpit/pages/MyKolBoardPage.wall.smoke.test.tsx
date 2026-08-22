import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
  recent_videos: { status: "ready", limit: 60, items: WALL_ITEMS, basis: "vkpi_kol_video_evidence 收藏集最近采集(封顶 60)" },
};

// 单 KOL 视图(/kol-pool/{id}/videos 同源端点):Alpha 两条 / Beta 零条
const KOL_VIDEOS: Record<string, unknown[]> = {
  "101": [
    { evidence_id: 9001, id: 9001, kol_pool_id: 101, project_id: 7, title: "On set with the new lens", view_count: 1000, like_count: 10, has_final_v1_cache: true, publish_date: "2026-07-01", content_url: "https://youtu.be/a1" },
    { evidence_id: 9003, id: 9003, kol_pool_id: 101, project_id: null, title: "daily vlog", view_count: 500, like_count: 3, has_final_v1_cache: false, publish_date: "2026-06-10", content_url: "https://youtu.be/a3" },
  ],
  "102": [],
};

function routeApi(overrides: { boardExt?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/my-kol/aggregate")) return AGG;
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext")) {
      const value = overrides.boardExt ?? EXT;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/marketing/channels/official-matrix")) return MATRIX;
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
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}
const TRACK_CALLS: Array<{ poolId: number; body: { content_url: string; product_skus: string[] } }> = [];
const TRACKED = new Set<number>();

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
    const more = screen.getByText(/查看更多/);
    expect(more.textContent).toContain("已显 12 / 当前已采集 14");
    fireEvent.click(more);
    await waitFor(() => {
      expect(wallCards().length).toBe(14);
      expect(screen.queryByText(/查看更多/)).toBeNull();
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
    fireEvent.click(screen.getByText(/查看更多/));
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

  it("单 KOL 视图:选中收藏 KOL 改走 /kol-pool/{id}/videos(库详情同源);零采集 KOL 诚实空;切回全部零重取", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByText("On set with the new lens")).toBeTruthy();
    expect(screen.getByText("daily vlog")).toBeTruthy();
    expect(screen.queryByText("Wall Coop Film")).toBeNull();
    const calls = () => apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calls().some((p) => p.includes("/my-kol/101/videos?"))).toBe(true);
    // 零采集 KOL → 板面空态口径(带 KOL 名,不透传后端字段)
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "102" } });
    expect(await screen.findByText(/Beta Vlog 暂无已采集内容——可在KOL详情发起补采。/)).toBeTruthy();
    // 切回全部:回 board-ext 聚合(已在手,不再发请求)
    const before = calls().length;
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "0" } });
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    expect(calls().length).toBe(before);
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
