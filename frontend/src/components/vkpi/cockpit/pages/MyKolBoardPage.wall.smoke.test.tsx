import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/my-kol/aggregate")) return AGG;
    if (p.startsWith("/api/admin/vkpi/my-kol/board-ext")) {
      const value = overrides.boardExt ?? EXT;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/marketing/channels/official-matrix")) return MATRIX;
    const videosMatch = p.match(/\/api\/admin\/vkpi\/kol-pool\/(\d+)\/videos/);
    if (videosMatch) {
      const items = KOL_VIDEOS[videosMatch[1]] || [];
      return { items, total: items.length, kol_pool_id: Number(videosMatch[1]) };
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
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
  routeApi();
});

describe("MyKolBoardPage 内容墙(contentWall:收藏集最近采集视频网格)", () => {
  it("网格真身:卡=缩略图链/标题/KOL 名/播放点赞 mono(NULL=未实测)/三档徽/已深析标;默认 12 张 + 查看更多增页", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    // 卡头短计数 = 后端条数真值;工具行三件套在场
    expect(screen.getAllByText("近 14 条").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("按 KOL 筛选")).toBeTruthy();
    expect(screen.getByText("仅 V 相关")).toBeTruthy();
    // 默认按最新排序:第 1 张=07-11 合作片,第 2 张=07-10 标题提及;默认恰 12 张
    expect(wallTitles().slice(0, 2)).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    expect(wallCards().length).toBe(12);
    // 点卡=原帖链接(新开页);缩略图=best_thumbnail 真图,其余三路皆无 → 诚实 ▶ 占位
    expect(wallCards()[0].getAttribute("href")).toBe("https://youtu.be/w1");
    expect(wallCards()[0].getAttribute("target")).toBe("_blank");
    expect(wallCards()[0].querySelector("img")?.getAttribute("src")).toBe("https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg");
    expect(wallCards()[1].querySelector("img")).toBeNull();
    // 播放读数诚实:NULL → 未实测(≠ 0;regex 会撞 SrcChip 口径行,收敛到卡本体)
    expect(wallCards()[1].textContent).toContain("▶ 未实测");
    expect(wallCards()[0].textContent).toContain("▶ 8,000");
    expect(screen.getByText("合作产出")).toBeTruthy();
    expect(screen.getByText("标题提及V")).toBeTruthy();
    expect(screen.getAllByText("未判定").length).toBe(10);
    expect(screen.getAllByText("已深析").length).toBe(1);
    // 增页:已显 12 / 14 → 点后全量 14,按钮消失
    const more = screen.getByText(/查看更多/);
    expect(more.textContent).toContain("已显 12 / 14");
    fireEvent.click(more);
    await waitFor(() => {
      expect(wallCards().length).toBe(14);
      expect(screen.queryByText(/查看更多/)).toBeNull();
    });
  });

  it("仅 V 相关 + 播放排序:未判定隐藏;实测播放降序、未实测排最后(不当 0 混序)", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.click(screen.getByText("仅 V 相关"));
    expect(wallTitles()).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    expect(screen.queryByText("Filler clip 1")).toBeNull();
    expect(screen.queryByText("未判定")).toBeNull();
    // 排序切「播放」(仍仅 V):8000 实测在前,NULL 未实测排最后
    fireEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(wallTitles()).toEqual(["Wall Coop Film", "VILTROX wall mention"]);
    // 关掉仅 V + 播放排序:填充片(100+i)按实测降序,未实测仍最后一张
    fireEvent.click(screen.getByText("仅 V 相关"));
    fireEvent.click(screen.getByText(/查看更多/));
    await waitFor(() => expect(wallCards().length).toBe(14));
    const titles = wallTitles();
    expect(titles[0]).toBe("Wall Coop Film");
    expect(titles[1]).toBe("Filler clip 12");
    expect(titles[titles.length - 1]).toBe("VILTROX wall mention");
  });

  it("单 KOL 视图:选中收藏 KOL 改走 /kol-pool/{id}/videos(库详情同源);零采集 KOL 诚实空;切回全部零重取", async () => {
    renderWall();
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "101" } });
    expect(await screen.findByText("On set with the new lens")).toBeTruthy();
    expect(screen.getByText("daily vlog")).toBeTruthy();
    expect(screen.queryByText("Wall Coop Film")).toBeNull();
    const calls = () => apiFetchMock.mock.calls.map((call) => String(call[0]));
    expect(calls().some((p) => p.includes("/kol-pool/101/videos"))).toBe(true);
    // 零采集 KOL → 板面空态口径(带 KOL 名,不透传后端字段)
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "102" } });
    expect(await screen.findByText(/Beta Vlog 暂无采集视频——在库行发起采集。/)).toBeTruthy();
    // 切回全部:回 board-ext 聚合(已在手,不再发请求)
    const before = calls().length;
    fireEvent.change(screen.getByLabelText("按 KOL 筛选"), { target: { value: "0" } });
    expect(await screen.findByText("Wall Coop Film")).toBeTruthy();
    expect(calls().length).toBe(before);
  });

  it("组失败/组空诚实降级:error → 该组聚合失败卡(带 reason);empty → 板面空态文案", async () => {
    routeApi({ boardExt: { ...EXT, recent_videos: { status: "error", reason: "recent_videos exploded" } } });
    renderWall();
    expect(await screen.findByText("该组聚合失败")).toBeTruthy();
    expect(screen.getByText(/recent_videos exploded/)).toBeTruthy();
    window.localStorage.clear();
    routeApi({ boardExt: { ...EXT, recent_videos: { status: "empty", reason: "收藏集内零 evidence——内容墙诚实空,不摆假卡。" } } });
    renderWall();
    expect(await screen.findByText("暂无采集视频——在库行发起采集。")).toBeTruthy();
  });
});
