// M1「让 0.4 秒的真结果真正被看见」的回归闸。
//
// 后端本地腿 0.3-0.4s 就交出真结果,面板却把它藏了 2-5 分钟:
//   (a) 清屏在 await 之前 → 等待期整块白屏,没有任何占位;
//   (b) 后台全网补充腿(20-25s)借用全局 executing 记忙 → 搜索按钮跟着转圈+禁用;
//   (c) 本地结果到手时没有任何「好了,可以看了」的信号。
// 下面按这三条各钉一颗钉子,外加输入框 Enter 键的裁定。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const domainMocks = vi.hoisted(() => ({
  deepCrawlKolUrl: vi.fn(), getKolSearchSession: vi.fn(), listKolSearchHistory: vi.fn(),
  smartKolSearch: vi.fn(), smartKolSearchProfileAdvanceJob: vi.fn(),
}));
const serviceMocks = vi.hoisted(() => ({
  archiveAllKolSearchHistory: vi.fn(), archiveKolSearchHistorySession: vi.fn(),
  restoreKolSearchHistorySession: vi.fn(), approveKolSearchSession: vi.fn(),
  createProjectDraftFromSession: vi.fn(), favoriteKolPool: vi.fn(),
  generateKolSearchSessionOutreach: vi.fn(), listKolPoolFavorites: vi.fn(), resolveKolPool: vi.fn(),
}));

vi.mock("../../../../domains/kol", () => domainMocks);
vi.mock("../../../../services/vkpi/kolPool-api", () => serviceMocks);

import { SmartKolInputPanel } from "./SmartKolInputPanel";
import { SmartKolSearchEntry } from "./SmartKolInputPanel.Entry";

function recallWith(handles: string[]) {
  return {
    method: "vector_recall",
    query: {},
    ratio: { creator_quota: 15, reviewer_quota: 15, policy: "smart", mixed_policy: "smart", dedupe: true },
    items: handles.map((handle, index) => ({
      kol_pool_id: 900 + index,
      handle,
      display_name: handle,
      platform: "youtube",
      followers: 12000 + index,
    })),
    buckets: { creator: [], reviewer: [] },
    diagnostics: {},
  };
}

/** 手动控制何时兑现的 promise,用来把「请求在飞」这一时刻钉住。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function runSearch(query = "35mm 低光人像") {
  fireEvent.change(screen.getByTestId("smart-kol-input"), { target: { value: query } });
  fireEvent.click(screen.getByTestId("smart-kol-run"));
}

describe("M1 · 本地结果的可见性", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.localStorage.clear();
    Object.values(domainMocks).forEach((mock) => mock.mockReset());
    Object.values(serviceMocks).forEach((mock) => mock.mockReset());
    domainMocks.listKolSearchHistory.mockResolvedValue({ items: [] });
    serviceMocks.listKolPoolFavorites.mockResolvedValue({ items: [], total: 0 });
    domainMocks.smartKolSearchProfileAdvanceJob.mockResolvedValue({ status: "queued" });
  });

  it("(a) 本地结果未到手时渲染骨架占位,而不是整块白屏", async () => {
    const pending = deferred<any>();
    domainMocks.smartKolSearch.mockReturnValue(pending.promise);

    render(<SmartKolInputPanel apiToken="token" />);
    runSearch();

    // 等待期:占位骨架在,结果区与「已就绪」抬头都还不该出现。
    expect(await screen.findByTestId("smart-kol-recall-skeleton")).toBeTruthy();
    expect(screen.queryByTestId("smart-kol-recall-ready")).toBeNull();

    pending.resolve({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: recallWith(["alpha", "bravo"]),
    });

    await waitFor(() => expect(screen.queryByTestId("smart-kol-recall-skeleton")).toBeNull());
  });

  it("(a) 上一轮结果不会被挂在新查询名下:新搜索一开跑,旧结果区就让位给骨架", async () => {
    domainMocks.smartKolSearch.mockResolvedValueOnce({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: recallWith(["alpha"]),
    });
    render(<SmartKolInputPanel apiToken="token" />);
    runSearch("第一次查询");
    expect(await screen.findByTestId("smart-kol-recall-ready")).toBeTruthy();

    const pending = deferred<any>();
    domainMocks.smartKolSearch.mockReturnValueOnce(pending.promise);
    runSearch("完全不同的第二次查询");

    expect(await screen.findByTestId("smart-kol-recall-skeleton")).toBeTruthy();
    expect(screen.queryByTestId("smart-kol-recall-ready")).toBeNull();
    pending.resolve({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 702 }, result: recallWith(["charlie"]),
    });
    await waitFor(() => expect(screen.queryByTestId("smart-kol-recall-skeleton")).toBeNull());
  });

  it("(b)(c) 本地结果一到手就报出可证明的人数,并立刻放开搜索按钮——哪怕后台补充还在跑", async () => {
    domainMocks.smartKolSearch.mockResolvedValue({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: recallWith(["alpha", "bravo", "charlie"]),
    });
    // 后台全网补充腿挂住不回:这正是线上那 20-25 秒。
    const advance = deferred<any>();
    domainMocks.smartKolSearchProfileAdvanceJob.mockReturnValue(advance.promise);

    render(<SmartKolInputPanel apiToken="token" />);
    runSearch();

    const ready = await screen.findByTestId("smart-kol-recall-ready");
    expect(ready.textContent).toContain("库内已找到 3 人");
    // 后台腿在跑:只给非阻塞的行内提示,不报进度百分比。
    expect(ready.textContent).toContain("后台继续补充新发现");
    expect(ready.textContent).not.toMatch(/%/);

    // 关键:后台腿还在飞,搜索按钮已经可用,用户可以马上发起下一次搜索。
    expect(screen.getByTestId("smart-kol-run")).not.toBeDisabled();

    advance.resolve({ status: "queued" });
    await waitFor(() => {
      expect(screen.getByTestId("smart-kol-recall-ready").textContent).not.toContain("后台继续补充新发现");
    });
  });

  it("(b) 后台补充在飞时点搜索按钮,能真的再发起一次本地查找", async () => {
    domainMocks.smartKolSearch.mockResolvedValue({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: recallWith(["alpha"]),
    });
    domainMocks.smartKolSearchProfileAdvanceJob.mockReturnValue(deferred<any>().promise);

    render(<SmartKolInputPanel apiToken="token" />);
    runSearch("第一次查询");
    await screen.findByTestId("smart-kol-recall-ready");
    expect(domainMocks.smartKolSearch).toHaveBeenCalledTimes(1);

    runSearch("第二次查询");
    await waitFor(() => expect(domainMocks.smartKolSearch).toHaveBeenCalledTimes(2));
  });

  it("(c) 本地腿失败时不报「已就绪」,也不把上一轮的人留在下面", async () => {
    domainMocks.smartKolSearch.mockResolvedValueOnce({
      status: "ready", mode: "text", query_type: "text_recall",
      search_session: { id: 701 }, result: recallWith(["alpha"]),
    });
    render(<SmartKolInputPanel apiToken="token" />);
    runSearch("第一次查询");
    await screen.findByTestId("smart-kol-recall-ready");

    domainMocks.smartKolSearch.mockRejectedValueOnce(new Error("上游超时"));
    runSearch("第二次查询");

    expect(await screen.findByText("上游超时")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("smart-kol-recall-ready")).toBeNull());
  });
});

describe("M1 · 输入框 Enter 键裁定", () => {
  function renderEntry(overrides: Partial<React.ComponentProps<typeof SmartKolSearchEntry>> = {}) {
    const onRun = vi.fn();
    render(
      <SmartKolSearchEntry
        value="35mm 低光人像"
        inferredMode="text"
        busy={false}
        disabled={false}
        onInputChange={() => {}}
        onRun={onRun}
        {...overrides}
      />,
    );
    return { onRun, input: screen.getByTestId("smart-kol-input") };
  }

  it("裸 Enter 不发起搜索(搜索会建会话、抓取、花钱,不接受打字误触)", () => {
    const { onRun, input } = renderEntry();
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(onRun).not.toHaveBeenCalled();
  });

  it("⌘/Ctrl+Enter 是明确手势,可发起搜索", () => {
    const meta = renderEntry();
    fireEvent.keyDown(meta.input, { key: "Enter", metaKey: true });
    expect(meta.onRun).toHaveBeenCalledTimes(1);
  });

  it("⌘+Enter 与按钮同一道闸:本地结果没到手(disabled)就不放行", () => {
    const { onRun, input } = renderEntry({ disabled: true, busy: true });
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    expect(onRun).not.toHaveBeenCalled();
  });

  it("中文输入法上屏候选词的那一下 Enter 不算发起搜索", () => {
    const { onRun, input } = renderEntry();
    fireEvent.keyDown(input, { key: "Enter", metaKey: true, isComposing: true });
    expect(onRun).not.toHaveBeenCalled();
  });
});
