import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

// 内容墙「去查最新内容」入口冒烟。守六条:
//  1. 报价先行:任何会花钱的点击之前,服务端报价都已经拿到手;
//  2. 确认边界 fail-closed:单个账号点即走(不弹框),「全部收藏」永远弹框——
//     服务端漏返回 requires_confirmation 也照样弹,绝不静默直发;
//  3. 诚实状态机:没派活时一个「正在取/正在抓」都不许出现;派活后如实回读结局,
//     被拦下就说被拦下,读不到就说读不到,**绝不冒充「全部回来」**;
//  4. 回读必须有终止条件:到上限就停,并如实说不再自动查;
//  5. 报价的计量单位 = 真实取数次数(YouTube 一个账号两次),不是「一个账号一次」;
//  6. 门面禁术语:界面文案不出现内部词与厂商名;额度与时间口径不依赖确认框。
// mock seam:services/http.apiFetch(与本目录其它冒烟同款)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { receiptSentence, WallFetchControl } from "./MyKolBoardPage.wall-fetch";

const BANNED = ["provider", "actor", "apify", "crawl", "job", "worker", "deep_crawl", "llm", "gemini"];
const PROGRESS_LIES = ["正在抓取", "正在采集", "抓取中", "正在从", "已完成取数"];

function plan(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    days: 7,
    window_label: "最近 7 天",
    kol_pool_id: null,
    scope: "own",
    scope_label: "你收藏的账号",
    planned_count: 2,
    planned: [
      { kol_pool_id: 1, name: "甲", platform: "youtube", window_exactness: "date_pushdown" },
      { kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only" },
    ],
    // 一次 YouTube 取数在平台侧是两次;IG 空返回会兜底再取一次 → 上限比下限高。
    fetch_calls: {
      total: 3,
      max_total: 4,
      by_platform: {
        youtube: { accounts: 1, per_account: 2, per_account_max: 2, calls: 2 },
        instagram: { accounts: 1, per_account: 1, per_account_max: 2, calls: 1 },
      },
    },
    posts_per_account: 12,
    followups_suppressed: true,
    requires_confirmation: true,
    skipped: { shared_readonly: [], recently_fetched: [], per_click_cap: [], daily_cap: [] },
    skipped_counts: { shared_readonly: 1, recently_fetched: 3, per_click_cap: 0, daily_cap: 0 },
    candidates_total: 6,
    candidates_truncated: false,
    window: {
      since: "2026-08-18",
      max_posts: 12,
      exactness_counts: { date_pushdown: 1, recent_only: 1 },
      exactness_labels: {
        date_pushdown: "按发布时间在平台侧截取",
        recent_only: "只能取最近内容,平台不认发布时间",
      },
    },
    limits: { per_click: 12, daily: 40, daily_used: 4, daily_left: 36, cooldown_hours: 6 },
    budget: { configured: true, usage_ratio: 0.25, hard_stopped: false },
    plan_hash: "hash-abc",
    ...overrides,
  };
}

function dispatched(overrides: Record<string, unknown> = {}) {
  return {
    status: "dispatched",
    plan: plan(),
    queued: [{ kol_pool_id: 1, name: "甲", platform: "youtube", window_exactness: "date_pushdown", job_id: 1 }],
    already_queued: [],
    failed: [],
    counts: { planned: 2, queued: 1, already_queued: 0, failed: 0 },
    ...overrides,
  };
}

function outcome(overrides: Record<string, unknown> = {}) {
  return {
    status: "ok",
    items: [],
    counts: { waiting: 0, landed: 0, stopped: 0, unknown: 0 },
    unknown_job_ids: [],
    ...overrides,
  };
}

function control(props: Record<string, unknown> = {}) {
  return (
    <WallFetchControl
      apiToken="t"
      kolPoolId={0}
      kolLabel="全部收藏 KOL"
      days={7}
      dayLabel="滚动近 7 天"
      onRefreshWall={() => {}}
      {...(props as never)}
    />
  );
}

function pageText(): string {
  return document.body.textContent || "";
}

function statusCalls(): number {
  return apiFetchMock.mock.calls.filter((call) => String(call[0]).includes("/wall-fetch/status")).length;
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("内容墙去查最新内容", () => {
  it("首屏不派活、不报价,也绝不显示进行时的假进度", () => {
    render(control());
    expect(apiFetchMock).not.toHaveBeenCalled();
    PROGRESS_LIES.forEach((lie) => expect(pageText()).not.toContain(lie));
  });

  it("「全部收藏」必须先报价再弹确认框,确认框里的数字原样来自服务端", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));

    const dialog = await screen.findByRole("dialog");
    // 报价一次(GET),此时一条活都没派出去。
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(String(apiFetchMock.mock.calls[0][0])).toContain("/my-kol/wall-fetch/plan");
    // 计量单位=真实取数次数:2 个账号却是 3 次,YouTube 那个要取两次。
    expect(dialog.textContent).toContain("本次会去 2 个账号取最新内容,合计 3 次取数(最多 4 次)");
    expect(dialog.textContent).toContain("YouTube 每个账号要取 2 次");
    expect(dialog.textContent).toContain("每个账号取最近 12 条");
    // 跳过明细如实摆出来,不静默丢。
    expect(dialog.textContent).toContain("1 个同事分享给你的,只能查看");
    expect(dialog.textContent).toContain("3 个最近刚取过,这次跳过");
    // 时间窗按平台分档:真截取的说真截取,近似的说近似,不糊成一句。
    expect(dialog.textContent).toContain("YouTube 1 个账号:按发布时间在平台侧截取");
    expect(dialog.textContent).toContain("Instagram 1 个账号:只能取最近内容,平台不认发布时间");
    PROGRESS_LIES.forEach((lie) => expect(pageText()).not.toContain(lie));
  });

  it("「全部收藏」= 服务端漏返回确认字段也照样弹框(fail-closed,绝不静默直发)", async () => {
    apiFetchMock.mockResolvedValueOnce(plan({ requires_confirmation: undefined }));
    render(control({ kolPoolId: 0 }));

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));

    await screen.findByRole("dialog");
    // 只有报价那一次 GET;一条活都没派出去。
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(String(apiFetchMock.mock.calls[0][0])).toContain("/wall-fetch/plan");
  });

  it("确认后回执只说「已安排 / 还没回来」,不冒充已经取完", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockResolvedValueOnce({
      ...dispatched(),
      already_queued: [{ kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only", job_id: 2 }],
      counts: { planned: 2, queued: 1, already_queued: 1, failed: 0 },
    });
    // 回读:两条都还在等——如实说还在等,不许说全部回来。
    apiFetchMock.mockResolvedValue(outcome({
      items: [
        { job_id: 1, kol_pool_id: 1, state: "waiting", reason_human: null },
        { job_id: 2, kol_pool_id: 2, state: "waiting", reason_human: null },
      ],
      counts: { waiting: 2, landed: 0, stopped: 0, unknown: 0 },
    }));
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认去 2 个账号取/ }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("已安排 1 个账号去取最新内容"));
    const receipt = screen.getByRole("status").textContent || "";
    // 回读说两条都还在等,回执就只能说还在等——一个字都不许说「回来了」。
    expect(receipt).toContain("2 个还在等");
    expect(receipt).toContain("还在等结果回来");
    expect(receipt).not.toContain("取回来了");
    expect(receipt).toContain("已经有一次在排队,本次并入那一次");
    PROGRESS_LIES.forEach((lie) => expect(receipt).not.toContain(lie));
    // POST 必须原样回传报价指纹与条数,前端不自己算。
    const body = JSON.parse(String(apiFetchMock.mock.calls[1][1].body));
    expect(body).toMatchObject({ plan_hash: "hash-abc", expected_count: 2, days: 7 });
    // 回读用的是既有派单号,不是新造一套进度真源。
    await waitFor(() => expect(statusCalls()).toBeGreaterThan(0));
    expect(String(apiFetchMock.mock.calls[2][0])).toContain("job_ids=1%2C2");
  });

  it("回读之前说「还没有结果回来」,回读之后就不再说——两句话不能互相打脸", () => {
    const pending = receiptSentence(dispatched() as never).text;
    const settled = receiptSentence(dispatched() as never, false).text;
    expect(pending).toContain("已安排 1 个账号去取最新内容,还没有结果回来");
    expect(settled).toContain("已安排 1 个账号去取最新内容");
    expect(settled).not.toContain("还没有结果回来");
  });

  it("派出去的活被拦下 → 回执如实说没能取到 + 为什么,绝不算进「已完成」", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockResolvedValueOnce(dispatched({
      queued: [
        { kol_pool_id: 1, name: "甲", platform: "youtube", window_exactness: "date_pushdown", job_id: 1 },
        { kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only", job_id: 2 },
      ],
      counts: { planned: 2, queued: 2, already_queued: 0, failed: 0 },
    }));
    apiFetchMock.mockResolvedValue(outcome({
      items: [
        { job_id: 1, kol_pool_id: 1, state: "landed", reason_human: null },
        { job_id: 2, kol_pool_id: 2, state: "stopped", reason_human: "本月取数额度已用完,这一条没能开始" },
      ],
      counts: { waiting: 0, landed: 1, stopped: 1, unknown: 0 },
    }));
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认去 2 个账号取/ }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("1 个没能取到"));
    const receipt = screen.getByRole("alert").textContent || "";
    expect(receipt).toContain("1 个已经取回来了");
    expect(receipt).toContain("本月取数额度已用完,这一条没能开始");
    expect(receipt).not.toContain("全部回来");
    // 有结局之后就不许再说「还没有结果回来」:两句话不能互相打脸。
    expect(receipt).not.toContain("还没有结果回来");
  });

  it("回读不到结果时算「读不到」,不算「已取回」", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockResolvedValueOnce(dispatched());
    apiFetchMock.mockResolvedValue(outcome({
      counts: { waiting: 0, landed: 0, stopped: 0, unknown: 1 },
      unknown_job_ids: [1],
    }));
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认去 2 个账号取/ }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("1 个这里读不到结果"));
    expect(screen.getByRole("status").textContent).not.toContain("已经取回来");
  });

  it("回读有终止条件:到上限就停下,并如实说不再自动查", async () => {
    vi.useFakeTimers();
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockResolvedValueOnce(dispatched());
    // 永远「还在等」:没有终止条件的话这里就是一个转不完的圈。
    apiFetchMock.mockResolvedValue(outcome({
      items: [{ job_id: 1, kol_pool_id: 1, state: "waiting", reason_human: null }],
      counts: { waiting: 1, landed: 0, stopped: 0, unknown: 0 },
    }));
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.click(screen.getByRole("button", { name: /确认去 2 个账号取/ }));
    for (let tick = 0; tick < 14; tick += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    }

    expect(statusCalls()).toBe(10);
    expect(screen.getByRole("status").textContent).toContain("1 个还在等");
    expect(screen.getByRole("status").textContent).toContain("不再自动查了");
    // 再推进一整分钟也不会多发一次:圈是真的停了。
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(statusCalls()).toBe(10);
  });

  it("单个账号点即走,不弹确认框", async () => {
    apiFetchMock.mockResolvedValueOnce(plan({ requires_confirmation: false, planned_count: 1, kol_pool_id: 2 }));
    apiFetchMock.mockResolvedValueOnce(dispatched({
      queued: [{ kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only", job_id: 3 }],
      counts: { planned: 1, queued: 1, already_queued: 0, failed: 0 },
    }));
    apiFetchMock.mockResolvedValue(outcome({
      items: [{ job_id: 3, kol_pool_id: 2, state: "waiting", reason_human: null }],
      counts: { waiting: 1, landed: 0, stopped: 0, unknown: 0 },
    }));
    render(control({ kolPoolId: 2, kolLabel: "乙" }));

    fireEvent.click(screen.getByRole("button", { name: /去查这个账号的最新内容/ }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("已安排 1 个账号"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("单账号路径不弹框,时间口径与额度照样看得见", async () => {
    // 点之前就有一句通用口径:不许让人以为「最近 7 天」处处都是精确过滤。
    render(control({ kolPoolId: 2, kolLabel: "乙" }));
    expect(pageText()).toContain("Instagram 只能取最近内容,平台不认发布时间");

    apiFetchMock.mockResolvedValueOnce(plan({
      requires_confirmation: false,
      planned_count: 1,
      planned: [{ kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only" }],
      window: {
        since: "2026-08-18",
        max_posts: 12,
        exactness_counts: { recent_only: 1 },
        exactness_labels: {
          date_pushdown: "按发布时间在平台侧截取",
          recent_only: "只能取最近内容,平台不认发布时间",
        },
      },
      budget: { configured: true, usage_ratio: 1, hard_stopped: true },
    }));
    apiFetchMock.mockResolvedValueOnce(dispatched({
      queued: [],
      failed: [{ kol_pool_id: 2, name: "乙", platform: "instagram", window_exactness: "recent_only", reason: "profile_url_missing" }],
      counts: { planned: 1, queued: 0, already_queued: 0, failed: 1 },
    }));
    fireEvent.click(screen.getByRole("button", { name: /去查这个账号的最新内容/ }));

    // 服务端算出的「本月额度已用完」不许在路上丢掉,单账号路径也要显示。
    await waitFor(() => expect(pageText()).toContain("本月取数额度已用完"));
    expect(pageText()).toContain("Instagram 1 个账号:只能取最近内容,平台不认发布时间");
  });

  it("连点只发一次报价:报价未回来之前按钮是禁用的", async () => {
    let release: (value: unknown) => void = () => {};
    apiFetchMock.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    render(control({ kolPoolId: 2, kolLabel: "乙" }));

    const button = screen.getByRole("button", { name: /去查这个账号的最新内容/ });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /正在算这次取几个/ })).toBeDisabled();
    release(plan({ requires_confirmation: false, planned_count: 0, planned: [] }));
    await waitFor(() => expect(screen.getByRole("status")).toBeTruthy());
  });

  it("算出来没有可取的账号时,如实说没派出去,并且一条都不派", async () => {
    apiFetchMock.mockResolvedValueOnce(plan({
      planned_count: 0,
      planned: [],
      skipped_counts: { shared_readonly: 0, recently_fetched: 2, per_click_cap: 0, daily_cap: 0 },
    }));
    render(control({ kolPoolId: 2, kolLabel: "乙" }));

    fireEvent.click(screen.getByRole("button", { name: /去查这个账号的最新内容/ }));

    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("没有派出取数"));
    expect(screen.getByRole("status").textContent).toContain("2 个最近刚取过,这次跳过");
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
  });

  it("报价在确认期间漂了 → 409:如实说没派出去,让人重看数字", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockRejectedValueOnce({ detail: { code: "wall_fetch_plan_drifted" } });
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认去 2 个账号取/ }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("这次没有派出"));
    expect(screen.getByRole("alert").textContent).toContain("请再点一次重新看数字");
  });

  it("未映射的机器错误码不许原样打到门面上", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    apiFetchMock.mockRejectedValueOnce({ detail: { code: "totally_unmapped_code_x" } });
    render(control());

    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认去 2 个账号取/ }));

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("没能派出"));
    expect(screen.getByRole("alert").textContent).not.toContain("totally_unmapped_code_x");
    expect(screen.getByRole("alert").textContent).toContain("原因还没查清");
  });

  it("门面文案不出现内部词与厂商名", async () => {
    apiFetchMock.mockResolvedValueOnce(plan());
    render(control());
    fireEvent.click(screen.getByRole("button", { name: /去查全部收藏账号的最新内容/ }));
    await screen.findByRole("dialog");

    const text = pageText().toLowerCase();
    BANNED.forEach((word) => expect(text).not.toContain(word));
    ["队列", "作业", "任务", "深爬"].forEach((word) => expect(pageText()).not.toContain(word));
  });
});
