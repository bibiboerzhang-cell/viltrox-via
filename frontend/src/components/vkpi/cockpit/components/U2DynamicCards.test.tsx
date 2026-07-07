import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// U2 四卡升级渲染 smoke(mock 数据,hermetic 不打后端)。
// 覆盖:BrandPulse(SoV Sparkline + 新鲜度呼吸灯)/ MorningBrief(呼吸灯 + DeltaBadge + 骨架屏)
//      / WorkerDevices(在线灯改呼吸灯,离线灰静态)/ SemanticRecall(加载态骨架屏)。
// 数据契约零改动:mock 形状与各卡现有类型一致。reduced-motion 的 CSS 降级卫兵
// 断言在 ui/UiPrimitives.test.tsx(media query 覆盖全部动画类)。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
}));

import { BrandPulsePanel } from "./BrandPulsePanel";
import { MorningBriefCard } from "./MorningBriefCard";
import { WorkerDevicesPanel } from "./WorkerDevicesPanel";
import { SemanticRecallCard } from "./SemanticRecallCard";

beforeEach(() => {
  apiFetch.mockReset();
  localStorage.clear();
});

describe("BrandPulsePanel · SoV 周曲线 + 新鲜度呼吸灯", () => {
  it("ready 数据 → SoV chip + Sparkline + fresh 呼吸灯(title 给具体时间)", async () => {
    const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    apiFetch.mockResolvedValue({
      status: "ready",
      window_days: 90,
      viltrox: {
        key: "viltrox", brand: "Viltrox", brand_type: "self",
        total_videos: 10, kol_count: 5, weekly: [1, 2, 3, 4],
        trend: "rising", momentum_pct: 20,
        share_of_voice: 0.25, rank: 2, brand_count_ranked: 8,
        top_examples: [{ title: "AF 85mm 实拍", posted_at: recent }],
      },
      brands: [{
        key: "sony", brand: "Sony", brand_type: "oem",
        total_videos: 20, kol_count: 9, weekly: [3, 4, 5, 6],
        trend: "stable", momentum_pct: 0, top_examples: [],
      }],
      groups: { rising: ["viltrox"], falling: [] },
      coverage: { videos_scanned: 100, brand_hit_videos: 30, sparse_weeks: 0 },
    });
    const { container } = render(<BrandPulsePanel apiToken="tok" />);
    expect(await screen.findByText(/品牌脉搏/)).toBeInTheDocument();
    expect(screen.getByText("SoV 25%")).toBeInTheDocument();
    // SoV 周曲线(客户端派生,4 周全有品牌提及 → 可画)
    expect(container.querySelector('[data-ui="sparkline"]')).toBeTruthy();
    expect(container.querySelector("path.vk-spark-path")!.getAttribute("d")).not.toContain("NaN");
    // 新鲜度呼吸灯:最近 1h 的证据 → fresh + 呼吸 + 具体时间 title
    const dot = container.querySelector('[data-ui="freshness-dot"]')!;
    expect(dot.getAttribute("data-state")).toBe("fresh");
    expect(dot.className).toContain("vk-breathe");
    expect(dot.getAttribute("title")).toContain("更新于");
  });

  it("no_brand_signal → 诚实空态,不画 Sparkline/呼吸灯", async () => {
    apiFetch.mockResolvedValue({ status: "no_brand_signal", window_days: 90 });
    const { container } = render(<BrandPulsePanel apiToken="tok" />);
    expect(await screen.findByText(/未命中品牌词表/)).toBeInTheDocument();
    expect(container.querySelector('[data-ui="sparkline"]')).toBeNull();
    expect(container.querySelector('[data-ui="freshness-dot"]')).toBeNull();
  });
});

describe("MorningBriefCard · 呼吸灯 + DeltaBadge + 骨架屏", () => {
  it("ready 数据 → headline + fresh 呼吸灯 + 较上次 ↑ 徽章(基线写回)", async () => {
    localStorage.setItem("vkpi:delta:brief.scrape", "2");
    apiFetch.mockResolvedValue({
      status: "ready",
      headline: "昨晚完成 12 件",
      generated_at: new Date().toISOString(),
      window: { hours: 16, timezone: "America/Los_Angeles" },
      totals: { scrape_done: 5, deep_done: 3, comments_new: 0, kol_new: 2, alerts_new: 0, failed_jobs: 0 },
      sections: [
        { key: "anomalies", status: "empty" },
        { key: "alerts", open_now: 2 },
      ],
    });
    const { container } = render(<MorningBriefCard apiToken="tok" />);
    expect(await screen.findByText("昨晚完成 12 件")).toBeInTheDocument();
    // 抓取 2 → 5:↑3;其余无基线 → 安静缺席
    expect(screen.getByText("↑3")).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("vkpi:delta:brief.scrape")).toBe("5"));
    const dot = container.querySelector('[data-ui="freshness-dot"]')!;
    expect(dot.getAttribute("data-state")).toBe("fresh");
    expect(dot.getAttribute("title")).toContain("晨报数据");
  });

  it("加载期 → 骨架屏替代「…」文字;数据到位即消失", async () => {
    let resolve!: (v: unknown) => void;
    apiFetch.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { container } = render(<MorningBriefCard apiToken="tok" />);
    expect(container.querySelector('[data-ui="skeleton"]')).toBeTruthy();
    resolve({ status: "ready", headline: "昨晚完成 1 件", totals: {}, sections: [] });
    expect(await screen.findByText("昨晚完成 1 件")).toBeInTheDocument();
    expect(container.querySelector('[data-ui="skeleton"]')).toBeNull();
  });
});

describe("WorkerDevicesPanel · 在线灯改呼吸灯", () => {
  it("在线=绿呼吸(title 口径零变);离线=灰静态", async () => {
    apiFetch.mockResolvedValue({
      status: "ready",
      devices: [
        {
          device_id: "d1", device_name: "Mac-A", platform: "darwin", trust_level: 2,
          last_seen_at: new Date().toISOString(), online: true,
          current_task: { task_type: "video_download", job_id: 7 }, lease_stats: { validated: 3 },
        },
        { device_id: "d2", device_name: "Mac-B", last_seen_at: "2026-01-01T00:00:00Z", online: false },
      ],
      recent_leases: [],
      task_type_counts: [],
    });
    const { container, unmount } = render(<WorkerDevicesPanel apiToken="tok" />);
    expect(await screen.findByText("Mac-A")).toBeInTheDocument();
    expect(screen.getByText(/在线 1\/2/)).toBeInTheDocument();
    const dots = Array.from(container.querySelectorAll('[data-ui="freshness-dot"]'));
    expect(dots.length).toBe(2);
    const online = dots.find((d) => d.getAttribute("data-state") === "fresh")!;
    const offline = dots.find((d) => d.getAttribute("data-state") === "stale")!;
    expect(online.className).toContain("vk-breathe");
    expect(online.getAttribute("title")).toBe("在线(5 分钟内有心跳)");
    expect(offline.className).toContain("bg-slate-600");
    expect(offline.className).not.toContain("vk-breathe");
    expect(offline.getAttribute("title")).toBe("离线(5 分钟内无心跳)");
    unmount(); // 清 30s 轮询 interval
  });
});

describe("SemanticRecallCard · 加载态骨架屏", () => {
  it("召回中 → 三段 chips + 候选行骨架;结果到位骨架消失、候选渲染", async () => {
    let resolve!: (v: unknown) => void;
    apiFetch.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { container } = render(<SemanticRecallCard apiToken="tok" />);
    const input = screen.getByPlaceholderText(/描述你要找的创作者/);
    fireEvent.change(input, { target: { value: "cinematic wedding" } });
    fireEvent.click(screen.getByText("找人"));
    await waitFor(() =>
      expect(container.querySelectorAll('[data-ui="skeleton"]').length).toBeGreaterThan(0),
    );
    resolve({
      status: "ready",
      query: { query_text: "cinematic wedding", limit: 20 },
      items: [{
        rank: 1, kol_pool_id: 11, display_name: "Alice", platform: "youtube",
        followers: 12000, why_fit: "婚礼电影感,机位调度成熟",
      }],
      stages: {
        recall: { method: "embedding_v1", candidates: 40 },
        coarse: { n: 30, with_dims_cosine: 12 },
        rerank: { status: "ok", top_n: 20, cost_note: "$0.02" },
      },
    });
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-ui="skeleton"]').length).toBe(0);
    expect(screen.getByText(/① 向量召回/)).toBeInTheDocument();
  });
});
