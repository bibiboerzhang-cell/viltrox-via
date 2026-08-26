import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// 单品播放数据「逐条重新实测」冒烟(诚实优先于快):
// - 重新实测是异步排队:点一下只出「已排队」,**绝不允许**出现「正在实测」式假同步;
// - 花钱前先算账:任何取数入口都先走服务端报价(纯读),确认框里的数字全部来自服务端;
// - 三道闸(单次上限 / 每日上限 / 冷却)在服务端判,被挡下时界面如实说被挡下的原因;
// - 防连点两层:请求在途 → 按钮禁用显「提交中…」;服务端已排队 → 按钮禁用显「已排队」;
// - 权限如实:同事共享的红人 can_refresh=false → 按钮禁用 + 人话原因,不是静默 403;
// - 收工判据分三态:全部成功 / 部分成功(说清几条没成功)/ 全部没成功,绝不谎报「全部回来」;
// - 未映射的机器码一律走统一兜底句,不许原样打到门面上;
// - 时间按浏览器时区(title 是绝对时间),门面不出现任何内部术语。
// mock seam:services/http.apiFetch(与本页其它冒烟同款)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { formatLocal } from "../../lib/timeLocal";
import { SkuPlayModule } from "./MyKolBoardPage.sku-play";

const OVERVIEW_PATH = "/api/admin/vkpi/my-kol/sku-play-overview";
const PLAN_PATH = "/api/admin/vkpi/my-kol/sku-play-refresh/plan";
const RUN_PATH = "/api/admin/vkpi/my-kol/sku-play-refresh";
const MEASURED_AT = "2026-08-22T10:00:00+00:00";

/** 内部术语黑名单(红线):门面上一个都不许出现。 */
const JARGON = [
  "apify", "provider", "worker", "job", "crawl", "deep_crawl", "actor",
  "llm", "gemini", "openai", "anthropic", "payload", "evidence_id", "kol_pool_id",
];

const IDLE_TASK = {
  status: "not_requested", job_id: null, requested_at: null, updated_at: null,
  data: { status: "stale", freshness: "stale", updated_at: MEASURED_AT, superseded_by_job: false },
};

function overview(overrides: Record<string, unknown> = {}) {
  return {
    contract: "my_kol_sku_play_overview_v1",
    generated_at: "2026-08-23T01:00:00+00:00",
    summary: { skus: 1, videos: 3, kols: 2, measured_videos: 2 },
    groups: [
      {
        sku_code: "AF85F14-Z",
        sku_name: "AF 85mm F1.4 Pro",
        videos: 3,
        kols: 2,
        latest_measured_at: MEASURED_AT,
        total_views: 13579,
        delta: { d1: null, d7: 2468, d30: null },
        refreshable_videos: 2,
        in_flight_videos: 0,
        items: [
          {
            evidence_id: 501, kol_pool_id: 101, kol_name: "Alpha Cam", platform: "youtube",
            title: "85mm 实拍评测", content_url: "https://youtube.com/watch?v=abc",
            view_count: 12345, like_count: 678, measured_at: MEASURED_AT,
            delta: { d1: 100, d7: 2468, d30: null },
            tracking_status: "active", link_relation_type: "manual",
            can_refresh: true, refresh_cadence_hours: 24, recently_measured: false,
            refresh: { ...IDLE_TASK },
          },
          {
            evidence_id: 502, kol_pool_id: 101, kol_name: "Alpha Cam", platform: "youtube",
            title: "开箱短片", content_url: "https://youtube.com/watch?v=def",
            view_count: 1234, like_count: null, measured_at: MEASURED_AT,
            delta: { d1: null, d7: -20, d30: null },
            tracking_status: "active", link_relation_type: "detected",
            can_refresh: true, refresh_cadence_hours: 24, recently_measured: false,
            refresh: { ...IDLE_TASK },
          },
          {
            evidence_id: 503, kol_pool_id: 909, kol_name: "共享红人", platform: "tiktok",
            title: "同事分享的视频", content_url: "https://tiktok.com/@x/video/3",
            view_count: null, like_count: null, measured_at: null,
            delta: { d1: null, d7: null, d30: null },
            tracking_status: "active", link_relation_type: "confirmed",
            can_refresh: false, refresh_forbidden_reason: "my_kol_paid_action_write_forbidden",
            refresh_cadence_hours: 168, recently_measured: false,
            refresh: {
              status: "not_requested", job_id: null, requested_at: null, updated_at: null,
              data: { status: "none", freshness: "never", updated_at: null, superseded_by_job: false },
            },
          },
        ],
      },
    ],
    truncated: false,
    empty_reason: null,
    ...overrides,
  };
}

/** 结局版总览:把被盯的两行换成终局任务态(用来钉收工横幅)。 */
function settledOverview(states: Record<number, Record<string, unknown>>) {
  const body = overview();
  body.groups[0].items.forEach((item) => {
    const next = states[item.evidence_id];
    if (next) (item as Record<string, unknown>).refresh = { ...IDLE_TASK, ...next };
  });
  return body;
}

const PLANNED = [
  { evidence_id: 501, kol_pool_id: 101, kol_name: "Alpha Cam", platform: "youtube", title: "85mm 实拍评测" },
  { evidence_id: 502, kol_pool_id: 101, kol_name: "Alpha Cam", platform: "youtube", title: "开箱短片" },
];

function plan(overrides: Record<string, unknown> = {}) {
  const planned = (overrides.planned as typeof PLANNED) || PLANNED;
  return {
    status: "ok",
    sku_code: "AF85F14-Z",
    evidence_id: null,
    planned,
    planned_count: planned.length,
    fetch_per_video: 1,
    fetch_calls_total: planned.length,
    requires_confirmation: true,
    skipped: {},
    skipped_counts: {},
    candidates_total: 3,
    candidates_truncated: false,
    limits: { per_click: 12, daily: 40, daily_used: 0, daily_left: 40, cooldown_hours: 6 },
    plan_hash: "hash-abc",
    ...overrides,
  };
}

interface RouteOptions {
  body?: unknown;
  bodies?: unknown[];
  plan?: (path: string) => unknown | Promise<unknown>;
  run?: (body: Record<string, unknown>) => unknown | Promise<unknown>;
}

const planCalls: string[] = [];
const runCalls: Record<string, unknown>[] = [];

function route(options: RouteOptions = {}) {
  planCalls.length = 0;
  runCalls.length = 0;
  let overviewReads = 0;
  apiFetchMock.mockReset().mockImplementation(async (path: unknown, init?: unknown) => {
    const p = String(path);
    if (p === OVERVIEW_PATH) {
      const list = options.bodies;
      const answer = list ? list[Math.min(overviewReads, list.length - 1)] : (options.body ?? overview());
      overviewReads += 1;
      return answer;
    }
    if (p.startsWith(PLAN_PATH)) {
      planCalls.push(p);
      return options.plan ? options.plan(p) : plan();
    }
    if (p === RUN_PATH) {
      expect(String((init as { method?: string } | undefined)?.method || "")).toBe("POST");
      const body = JSON.parse(String((init as { body?: string } | undefined)?.body || "{}"));
      runCalls.push(body);
      if (options.run) return options.run(body);
      const planned = PLANNED.filter((item) => !body.evidence_id || item.evidence_id === body.evidence_id);
      return {
        status: "dispatched",
        plan: plan({ planned, planned_count: planned.length, fetch_calls_total: planned.length }),
        queued: planned,
        already_queued: [],
        failed: [],
        counts: { planned: planned.length, queued: planned.length, already_queued: 0, failed: 0 },
      };
    }
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

async function openGroup() {
  render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
  fireEvent.click(await screen.findByText("AF 85mm F1.4 Pro"));
  return await screen.findByText("85mm 实拍评测");
}

function rowOf(evidenceId: number): HTMLElement {
  const cell = document.querySelector(`[data-vkpi-sku-play-refresh="${evidenceId}"]`);
  if (!cell) throw new Error(`no refresh cell for ${evidenceId}`);
  return cell as HTMLElement;
}

function surfaceText(): string {
  return `${document.body.textContent || ""} ${Array.from(document.querySelectorAll("[title]"))
    .map((node) => node.getAttribute("title") || "")
    .join(" ")}`;
}

beforeEach(() => {
  route();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("单品播放 · 逐条重新实测", () => {
  it("点一行先算账再派活:回执如实说「已排队」,绝不说「正在实测」", async () => {
    await openGroup();
    fireEvent.click(within(rowOf(501)).getByRole("button"));

    // ① 先报价(纯读、零花费),报价里带上这一行;② 再按报价派活,回传指纹与条数。
    await waitFor(() => expect(runCalls.length).toBe(1));
    expect(planCalls[0]).toContain("sku_code=AF85F14-Z");
    expect(planCalls[0]).toContain("evidence_id=501");
    expect(runCalls[0]).toMatchObject({ sku_code: "AF85F14-Z", plan_hash: "hash-abc", expected_count: 2 });

    const receipt = await screen.findByText(/已排队/);
    expect(receipt.textContent || "").toContain("取数在后台进行");
    expect(document.body.textContent || "").not.toContain("正在实测");
    expect(document.body.textContent || "").not.toContain("实时更新");
    expect(receipt.textContent || "").not.toContain("已完成");
  });

  it("防连点①:请求在途时该行按钮禁用并显示「提交中…」,重复点击不再发请求", async () => {
    let release: (value: unknown) => void = () => {};
    route({ plan: () => new Promise((resolve) => { release = resolve; }) });
    await openGroup();
    fireEvent.click(within(rowOf(501)).getByRole("button"));

    await waitFor(() => expect(within(rowOf(501)).getByRole("button").textContent).toContain("提交中…"));
    expect((within(rowOf(501)).getByRole("button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(rowOf(501)).getByRole("button"));
    fireEvent.click(within(rowOf(501)).getByRole("button"));
    expect(planCalls.length).toBe(1);

    release(plan({ planned: [PLANNED[0]], evidence_id: 501 }));
    await waitFor(() => expect(runCalls.length).toBe(1));
    expect(planCalls.length).toBe(1);
  });

  it("防连点②:服务端已在队列里的行显示「已排队」且按钮禁用,不谎报又派了一次", async () => {
    const body = overview();
    (body.groups[0].items[0] as Record<string, unknown>).refresh = {
      status: "queued", job_id: 77, requested_at: MEASURED_AT, updated_at: MEASURED_AT,
      data: { status: "stale", freshness: "stale", updated_at: MEASURED_AT, superseded_by_job: true },
    };
    route({ body });
    await openGroup();

    const cell = rowOf(501);
    expect(within(cell).getByRole("status").textContent).toContain("重测中");
    const button = within(cell).getByRole("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toContain("已排队");
    fireEvent.click(button);
    expect(planCalls.length).toBe(0);
    expect(runCalls.length).toBe(0);
  });

  it("同事共享的红人:按钮禁用 + 人话原因,不是静默 403", async () => {
    await openGroup();
    const button = within(rowOf(503)).getByRole("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("title")).toBe("这是同事分享给你的红人,只能查看,不能从这里重新实测");
    fireEvent.click(button);
    expect(planCalls.length).toBe(0);
  });

  it("单品批量:先只算账,确认框写服务端算出的真实次数,确认后才派一次", async () => {
    await openGroup();
    fireEvent.click(screen.getByTitle(/先算一下这次会对几条视频重新取数/));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent || "").toContain("本次会对");
    expect(dialog.textContent || "").toContain("一共向平台取");
    expect(dialog.textContent || "").toContain("一次最多 12 条");
    expect(dialog.textContent || "").toContain("今天还可取 40 条");
    expect(runCalls.length).toBe(0);   // 只算账,一次取数都没发生

    fireEvent.click(within(dialog).getByText(/确认取 2 次/));
    await waitFor(() => expect(runCalls.length).toBe(1));
    expect(runCalls[0]).toMatchObject({ plan_hash: "hash-abc", expected_count: 2 });
    expect((await screen.findByText(/已排队 2 条/)).textContent).toBeTruthy();
  });

  it("超单次上限:确认框如实说被上限截掉几条,确认的次数就是服务端算的次数", async () => {
    route({
      plan: () => plan({
        planned: [PLANNED[0]],
        skipped_counts: { per_click_cap: 1 },
        limits: { per_click: 1, daily: 40, daily_used: 0, daily_left: 39, cooldown_hours: 6 },
      }),
    });
    await openGroup();
    fireEvent.click(screen.getByTitle(/先算一下这次会对几条视频重新取数/));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent || "").toContain("1 条超过单次上限(一次最多 1 条)");
    fireEvent.click(within(dialog).getByText(/确认取 1 次/));
    await waitFor(() => expect(runCalls.length).toBe(1));
    expect(runCalls[0].expected_count).toBe(1);
  });

  it("每日额度耗尽:确认框直说一条都不会去取,连确认按钮都没有", async () => {
    route({
      plan: () => plan({
        planned: [],
        skipped_counts: { daily_cap: 2 },
        limits: { per_click: 12, daily: 40, daily_used: 40, daily_left: 0, cooldown_hours: 6 },
      }),
    });
    await openGroup();
    fireEvent.click(screen.getByTitle(/先算一下这次会对几条视频重新取数/));

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent || "").toContain("本次一条都不会去取");
    expect(dialog.textContent || "").toContain("2 条今天的取数额度用完了(每天最多 40 条)");
    expect(within(dialog).queryByText(/确认取/)).toBeNull();
    expect(runCalls.length).toBe(0);
  });

  it("冷却生效:行内点击被服务端挡下时如实说刚测过,一次取数都不发生", async () => {
    route({
      plan: () => plan({
        planned: [], evidence_id: 501,
        skipped_counts: { recently_measured: 1 },
      }),
    });
    await openGroup();
    fireEvent.click(within(rowOf(501)).getByRole("button"));

    const receipt = await screen.findByText(/这次一条都没去取/);
    expect(receipt.textContent || "").toContain("1 条刚测过不久,6 小时内不重复取数");
    expect(runCalls.length).toBe(0);
  });

  it("取消确认 = 一次取数请求都不发", async () => {
    await openGroup();
    fireEvent.click(screen.getByTitle(/先算一下这次会对几条视频重新取数/));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByText("取消"));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(runCalls.length).toBe(0);
  });

  it("派活时单条没提交成功:如实计入没能提交,未知原因码不打到门面上", async () => {
    route({
      run: () => ({
        status: "dispatched",
        plan: plan(),
        queued: [PLANNED[0]],
        already_queued: [],
        failed: [{ ...PLANNED[1], reason: "kaboom_9000" }],
        counts: { planned: 2, queued: 1, already_queued: 0, failed: 1 },
      }),
    });
    await openGroup();
    fireEvent.click(within(rowOf(501)).getByRole("button"));

    const receipt = await screen.findByText(/1 条没能提交/);
    expect(receipt.textContent || "").toContain("原因暂时说不清");
    expect(document.body.textContent || "").not.toContain("kaboom_9000");
  });
});

describe("单品播放 · 收工判据(被拦下 / 失败绝不算成功)", () => {
  async function dispatchThenSettle(second: unknown) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    route({ bodies: [overview(), second] });
    render(<SkuPlayModule apiToken="t" noToken={<div>no token</div>} />);
    fireEvent.click(await screen.findByText("AF 85mm F1.4 Pro"));
    await screen.findByText("85mm 实拍评测");
    fireEvent.click(screen.getByTitle(/先算一下这次会对几条视频重新取数/));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByText(/确认取 2 次/));
    await waitFor(() => expect(runCalls.length).toBe(1));
    // 第一拍退避重读(3s)→ 父级重取总览 → 才允许下结论。
    await act(async () => { await vi.advanceTimersByTimeAsync(3200); });
  }

  it("部分成功:说清几条没成功和原因,绝不宣布「全部回来」", async () => {
    await dispatchThenSettle(settledOverview({
      501: {
        status: "ready", updated_at: MEASURED_AT,
        data: { status: "ready", freshness: "fresh", updated_at: MEASURED_AT, superseded_by_job: false },
      },
      502: { status: "blocked", failure_reason_human: "这条视频的账号本月取数额度用完了" },
    }));

    const banner = await screen.findByText(/1 条已取回并更新了读数/);
    expect(banner.textContent || "").toContain("1 条没成功");
    expect(banner.textContent || "").toContain("这条视频的账号本月取数额度用完了");
    expect(document.body.textContent || "").not.toContain("全部取回");
  });

  it("全部没成功:如实说一条都没取到,读数还是上一次的", async () => {
    await dispatchThenSettle(settledOverview({
      501: { status: "failed", failure_reason_human: "平台没返回这条视频的播放数据" },
      502: { status: "failed", failure_reason_human: "平台没返回这条视频的播放数据" },
    }));

    const banner = await screen.findByText(/这批 2 条都没能取到数/);
    expect(banner.textContent || "").toContain("平台没返回这条视频的播放数据");
    expect(banner.textContent || "").toContain("读数还是上一次的");
    expect(document.body.textContent || "").not.toContain("全部取回");
  });

  it("失败没给人话原因时走统一兜底句,不回显机器码", async () => {
    await dispatchThenSettle(settledOverview({
      501: { status: "failed" },
      502: { status: "failed" },
    }));

    const banner = await screen.findByText(/这批 2 条都没能取到数/);
    expect(banner.textContent || "").toContain("原因暂时说不清");
    expect(surfaceText().toLowerCase()).not.toContain("last_error");
  });

  it("全部成功才允许说全部取回", async () => {
    const ready = {
      status: "ready", updated_at: MEASURED_AT,
      data: { status: "ready", freshness: "fresh", updated_at: MEASURED_AT, superseded_by_job: false },
    };
    await dispatchThenSettle(settledOverview({ 501: ready, 502: ready }));

    const banner = await screen.findByText(/这批 2 条已经全部取回/);
    expect(banner.textContent || "").toContain("实测时间已按最新结果更新");
  });
});

describe("单品播放 · 门面口径", () => {
  it("最后实测时间按浏览器时区渲染(title = 绝对时间),不硬编码假新鲜度", async () => {
    await openGroup();
    const measured = document.querySelector(`[title="${formatLocal(MEASURED_AT)}"]`);
    expect(measured).toBeTruthy();
    expect(formatLocal(MEASURED_AT)).not.toBe("—");
    const button = within(rowOf(501)).getByRole("button");
    expect(button.getAttribute("title") || "").toContain("不会立刻出数");
  });

  it("门面零内部术语", async () => {
    await openGroup();
    fireEvent.click(within(rowOf(501)).getByRole("button"));
    await screen.findByText(/已排队/);
    const surface = surfaceText().toLowerCase();
    JARGON.forEach((word) => expect(surface.includes(word)).toBe(false));
  });
});
