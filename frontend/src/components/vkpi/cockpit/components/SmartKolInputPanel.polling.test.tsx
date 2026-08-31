// M2「治卡」轮询侧看门测试。
// 用户原话「为什么感觉现在好卡啊」——真因不是后端慢(本地腿 0.3-0.4 秒就交结果),
// 而是前端每 2.5 秒无条件把整棵结果树重画一遍。这里锁住三件事:
//   ① 后端这一拍没有新数据 → 一次 applyPolledSession 都不许再调(不再 mint 新会话对象 → 不再 setState);
//   ② 退避:2.5/2.5/5/5/10…,真有新数据或标签页回前台立刻退回队首,12 分钟总时限不变;
//   ③ 页面不可见 → 停发请求也停空转,回前台立刻补一拍。
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getSession = vi.hoisted(() => vi.fn());

vi.mock("../../../../domains/kol", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return { ...actual, getKolSearchSession: getSession };
});

import {
  SESSION_POLL_BACKOFF_MS,
  sessionPollDelayMs,
  useSmartKolSessionPolling,
} from "./SmartKolInputPanel.polling";
import {
  __resetSearchProgressStoreForTests,
  getSearchProgressSnapshot,
} from "./SmartKolInputPanel.progressStore";

function runningSession(itemCount: number) {
  return {
    id: 4242,
    query_text: "35mm f1.2 摄影师",
    query_type: "text_recall",
    status: "running",
    item_count: itemCount,
    items: Array.from({ length: itemCount }, (_, index) => ({
      id: 900 + index,
      item_type: "new_creator",
      status: "identified",
      stage: "identified",
      rank: index + 1,
      kol_pool_id: null,
      source_url: `https://www.youtube.com/@face_${index}`,
      payload: { source: "platform_discovery", platform: "youtube", handle: `face_${index}` },
    })),
    result_summary: {
      smart_search_profile_advance_job: { advance_status: "running", advance_counts: { ready: itemCount, executed: 0 } },
    },
  };
}

type Harness = ReturnType<typeof makeOptions>;

function makeOptions() {
  return {
    apiToken: "tok",
    pollingSearchSessionId: 4242 as number | null,
    applyPolledSession: vi.fn(),
    refreshHistory: vi.fn(async () => {}),
    setPollingSearchSessionId: vi.fn(),
    setPollPausedSessionId: vi.fn(),
    setSessionPollNotice: vi.fn(),
  };
}

function PollingHarness({ options }: { options: Harness }) {
  useSmartKolSessionPolling(options);
  return null;
}

/** 推进定时器并把随之而来的微任务(fetch 的 then / 下一次 schedule)排干。 */
async function advance(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function flush() {
  await advance(0);
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("useSmartKolSessionPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getSession.mockReset();
    __resetSearchProgressStoreForTests();
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  });

  afterEach(() => {
    vi.useRealTimers();
    __resetSearchProgressStoreForTests();
  });

  it("后端没有新数据时不再重复 applyPolledSession(整页重渲的源头)", async () => {
    // 每拍都返回内容相同、但对象是新克隆的 payload —— 这正是线上的形态:
    // 引用永远是新的,所以「引用比对」挡不住,必须按内容判。
    getSession.mockImplementation(async () => JSON.parse(JSON.stringify(runningSession(3))));
    const options = makeOptions();
    render(<PollingHarness options={options} />);
    await flush();
    expect(getSession).toHaveBeenCalledTimes(1);
    expect(options.applyPolledSession).toHaveBeenCalledTimes(1);

    await advance(2500);
    await advance(2500);
    await advance(5000);
    expect(getSession).toHaveBeenCalledTimes(4);
    // 三拍新请求、零次内容变化 → 容器一次 setState 都不该挨。
    expect(options.applyPolledSession).toHaveBeenCalledTimes(1);
    // 进度文案也没有每拍打在容器 state 上。
    expect(options.setSessionPollNotice).not.toHaveBeenCalled();
  });

  it("真有新数据时照常应用,并把退避退回队首", async () => {
    let itemCount = 3;
    getSession.mockImplementation(async () => JSON.parse(JSON.stringify(runningSession(itemCount))));
    const options = makeOptions();
    render(<PollingHarness options={options} />);
    await flush();
    expect(options.applyPolledSession).toHaveBeenCalledTimes(1);

    // 两拍无变化 → 退避已经走到 5000。
    await advance(2500);
    await advance(2500);
    expect(getSession).toHaveBeenCalledTimes(3);
    await advance(2500);
    expect(getSession).toHaveBeenCalledTimes(3);
    await advance(2500);
    expect(getSession).toHaveBeenCalledTimes(4);
    expect(options.applyPolledSession).toHaveBeenCalledTimes(1);

    // 新结果到达 → 应用 + 退避归零(下一拍又是 2500)。
    itemCount = 5;
    await advance(5000);
    expect(options.applyPolledSession).toHaveBeenCalledTimes(2);
    const callsBefore = getSession.mock.calls.length;
    await advance(2500);
    expect(getSession).toHaveBeenCalledTimes(callsBefore + 1);
  });

  it("进度文案走外部 store,不再每拍 setState 打在容器上", async () => {
    getSession.mockImplementation(async () => JSON.parse(JSON.stringify(runningSession(3))));
    const options = makeOptions();
    render(<PollingHarness options={options} />);
    await flush();
    const snapshot = getSearchProgressSnapshot();
    expect(snapshot.sessionId).toBe(4242);
    expect(snapshot.notice).toContain("阶段：");
    expect(options.setSessionPollNotice).not.toHaveBeenCalled();
  });

  it("同步失败也只换那一行文案,不把整棵结果树重画一遍", async () => {
    getSession.mockImplementation(async () => { throw new Error("网络抖动"); });
    const options = makeOptions();
    render(<PollingHarness options={options} />);
    await flush();
    expect(getSearchProgressSnapshot().notice).toBe("网络抖动");
    expect(options.setSessionPollNotice).not.toHaveBeenCalled();
    expect(options.applyPolledSession).not.toHaveBeenCalled();
  });

  it("页面不可见时既不发请求也不空转,回前台立刻补一拍", async () => {
    getSession.mockImplementation(async () => JSON.parse(JSON.stringify(runningSession(3))));
    const options = makeOptions();
    render(<PollingHarness options={options} />);
    await flush();
    expect(getSession).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    await advance(60000);
    expect(getSession).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await flush();
    expect(getSession).toHaveBeenCalledTimes(2);
  });

  it("卸载时复位实时快照,下一次搜索不会串到上一次的进度文案", async () => {
    getSession.mockImplementation(async () => JSON.parse(JSON.stringify(runningSession(3))));
    const options = makeOptions();
    const view = render(<PollingHarness options={options} />);
    await flush();
    expect(getSearchProgressSnapshot().notice).not.toBe("");
    await act(async () => { view.unmount(); });
    expect(getSearchProgressSnapshot()).toEqual({ sessionId: null, notice: "" });
  });
});

describe("sessionPollDelayMs", () => {
  it("按 2.5/2.5/5/5/10 退避,并在末项封顶", () => {
    expect(SESSION_POLL_BACKOFF_MS).toEqual([2500, 2500, 5000, 5000, 10000]);
    expect([0, 1, 2, 3, 4, 5, 99].map(sessionPollDelayMs)).toEqual([2500, 2500, 5000, 5000, 10000, 10000, 10000]);
    // 负数(不该出现)也不许退化成 0ms 死循环。
    expect(sessionPollDelayMs(-3)).toBe(2500);
  });
});
