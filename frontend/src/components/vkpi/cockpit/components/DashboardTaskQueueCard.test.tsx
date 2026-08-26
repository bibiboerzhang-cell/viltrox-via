import React from "react";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { apiFetch, fetchProgressCenter } = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  fetchProgressCenter: vi.fn(),
}));

vi.mock("../../../../services/http", () => ({ apiFetch }));
vi.mock("../../../../services/vkpi/progressCenter-api", () => ({ fetchProgressCenter }));

import { DashboardTaskQueueCard } from "./DashboardTaskQueueCard";

/**
 * 门面禁术语红线的机器守卫:厂商名 / 模型 id / 内部词一旦被写回卡面(正文、title、aria-label)
 * 这条正则就会红。将来有人再写 `Gemini` 或 `Provider`,CI 立刻拦下。
 */
const BANNED_ON_CHROME = /gemini|gpt-\d|claude|google|openai|anthropic|provider|worker|binding|fleet\s*breaker|single_call|llm|readiness|lexicon/i;

function chromeText(container: HTMLElement): string {
  const attrs = Array.from(container.querySelectorAll("[title], [aria-label]"))
    .map((el) => `${el.getAttribute("title") || ""} ${el.getAttribute("aria-label") || ""}`)
    .join(" ");
  return `${container.textContent || ""} ${attrs}`;
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "t-1",
    source: "ledger",
    kind: "账号深析",
    label: "youtube/@creator",
    status: "running",
    stage: "thinking",
    stage_label: "分析",
    created_at: "2026-07-09T11:59:00Z",
    updated_at: "2026-07-09T11:59:30Z",
    masked: false,
    progress_pct: 20,
    eta_seconds: 60,
    ...overrides,
  };
}

function progressPayload(overrides: Record<string, unknown> = {}) {
  const base = {
    status: "ready",
    generated_at: "2026-07-09T12:00:00Z",
    running: [] as unknown[],
    queued: [] as unknown[],
    recent_done: [],
    recent_llm: [],
    stage_flow: [],
    diagnostics: { worker_online: true },
    ...overrides,
  } as Record<string, unknown>;
  const running = base.running as unknown[];
  const queued = base.queued as unknown[];
  return {
    ...base,
    counts: (base.counts as unknown) || {
      running: running.length,
      queued: queued.length,
      active_total: running.length + queued.length,
      recent_total: 0,
    },
  };
}

function systemModelsPayload() {
  const bindings = [
    "google/gemini-3.6-flash",
    "google/gemini-3.5-flash-lite",
    "openai/gpt-5.6-luna",
    "anthropic/claude-sonnet-5",
  ];
  return {
    readiness_audit: {
      active_scope: {
        binding_count: bindings.length,
        bindings,
        production_ready_count: 0,
        runtime_authorized_count: bindings.length,
        runtime_blocked_count: 0,
      },
    },
    task_model_readiness: Object.fromEntries(bindings.map((binding, index) => [
      `task-${index}`,
      {
        binding,
        configured: true,
        production_ready: false,
        runtime_authorization: {
          allowed_by_model_readiness: true,
          source: "operator_ack",
          temporary: true,
        },
      },
    ])),
  };
}

function costPayload(overrides: Record<string, unknown> = {}) {
  return {
    today: { apify_calls: 1, llm_calls: 2, total_usd: 0.34 },
    budgets: {
      monthly_total: {
        configured: true,
        allowed: true,
        hard_stopped: false,
        cap_usd: 100,
        current_spend: 3,
        ...overrides,
      },
    },
  };
}

function httpError(status: number) {
  return Object.assign(new Error(`http ${status}`), { status });
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function setVisibility(value: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, value });
  Object.defineProperty(document, "hidden", { configurable: true, value: value === "hidden" });
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

function laneOf(name: string): HTMLElement {
  const node = screen.getByText(name).closest(".vkpi-dashboard-task-queue__lane");
  expect(node).toBeTruthy();
  return node as HTMLElement;
}

function headerStatus(container: HTMLElement): string {
  return container.querySelector("header > span")?.textContent || "";
}

beforeEach(() => {
  fetchProgressCenter.mockReset().mockResolvedValue(progressPayload({
    queued: [task({ id: "q-1", status: "queued", stage: "queued", stage_label: "排队", progress_pct: 0, eta_seconds: null })],
    counts: { running: 0, queued: 2, active_total: 2, recent_total: 0 },
  }));
  apiFetch.mockReset().mockImplementation((url: string) => (
    url === "/api/admin/system/models"
      ? Promise.resolve(systemModelsPayload())
      : Promise.resolve(costPayload())
  ));
});

describe("DashboardTaskQueueCard 泳道取值", () => {
  it("running 按 stage 落到对应泳道，queued 直接取 queued 数组", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      running: [
        task({ id: "s-1", stage: "search", kind: "评论采集", label: "creator-a", progress_pct: 30 }),
        task({ id: "th-1", stage: "thinking", kind: "账号深析", label: "creator-b", progress_pct: 40 }),
        task({ id: "th-2", stage: "thinking", kind: "账号深析", label: "creator-c", progress_pct: 60 }),
        task({ id: "sm-1", stage: "summarizing", kind: "结果落库", label: "creator-d", progress_pct: 90 }),
      ],
      queued: [
        task({ id: "q-1", status: "queued", stage: "queued" }),
        task({ id: "q-2", status: "queued", stage: "queued" }),
        task({ id: "q-3", status: "queued", stage: "queued" }),
      ],
    }));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);
    await screen.findByText("评论采集 · creator-a");

    expect(within(laneOf("抓取")).getByText("30%")).toBeInTheDocument();
    expect(within(laneOf("分析")).getByText("50%")).toBeInTheDocument();
    expect(within(laneOf("落库")).getByText("90%")).toBeInTheDocument();
    expect(within(laneOf("等待中")).getByText("3")).toBeInTheDocument();
    expect(laneOf("等待中").querySelector("i")).toHaveClass("is-waiting");
    expect(headerStatus(container)).toBe("4 处理中");
  });

  it("排队任务保持静态等待态，不伪装成处理中动画", async () => {
    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    await screen.findByText("2 等待中");
    const queuedLane = laneOf("等待中");
    expect(queuedLane).not.toHaveTextContent("12%");
    expect(queuedLane.querySelector("i")).toHaveClass("is-waiting");
    expect(queuedLane.querySelector("i")).not.toHaveClass("is-indeterminate");
    expect(container).toHaveTextContent("今日 3 次 · $0.34");
  });

  it("超历史均时保持不定态而不是伪装成 0%", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      running: [task({
        id: "2241",
        kind: "评论采集",
        label: "dianakenyeres",
        stage: "search",
        progress_pct: null,
        progress_overdue: true,
        progress_label: "已超历史均时",
        eta_seconds: null,
      })],
    }));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" compact />);

    expect(await screen.findByText("评论采集 · dianakenyeres")).toBeInTheDocument();
    expect(screen.getByText("#2241")).toBeInTheDocument();
    expect(screen.getByText("超均时")).toBeInTheDocument();
    expect(container).not.toHaveTextContent("0%");
    expect(container.querySelector(".vkpi-dashboard-task-queue__bar i")).toHaveClass("is-indeterminate");
  });
});

describe("DashboardTaskQueueCard 诚实空态", () => {
  it("队列真空时四条泳道给出「暂无…任务」与真实 0，不再显示破折号", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload());

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);
    await screen.findByText("暂无抓取任务");

    expect(screen.getByText("暂无分析任务")).toBeInTheDocument();
    expect(screen.getByText("暂无落库任务")).toBeInTheDocument();
    expect(screen.getByText("暂无排队任务")).toBeInTheDocument();
    expect(container.textContent || "").not.toContain("--");
    expect(container.textContent || "").not.toContain("—");
    expect(within(laneOf("抓取")).getByText("0")).toBeInTheDocument();
    expect(headerStatus(container)).toBe("基础配置正常 · 每个任务开跑前再确认");
  });

  it("紧凑态没有任务时也说清「当前没有在跑的任务」", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload());

    const { container } = render(<DashboardTaskQueueCard apiToken="token" compact />);

    expect(await screen.findByText("当前没有在跑的任务")).toBeInTheDocument();
    expect(container.textContent || "").not.toContain("--");
  });

  it("后台未运行时说明排队为何不动，不把 0 当作正常空闲", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      queued: [task({ id: "q-1", status: "queued", stage: "queued" }), task({ id: "q-2", status: "queued", stage: "queued" })],
      diagnostics: { worker_online: false },
    }));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    await screen.findByText("等待后台恢复");
    expect(headerStatus(container)).toBe("后台未运行 · 2 等待中");
    expect(laneOf("等待中")).toHaveClass("is-blocked");
  });

  it("月度预算硬停时显示当前受限，不把无任务误报成基础配置正常", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload());
    apiFetch.mockImplementation((url: string) => (
      url === "/api/admin/system/models"
        ? Promise.resolve(systemModelsPayload())
        : Promise.resolve(costPayload({ allowed: false, hard_stopped: true }))
    ));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("已阻断")).toBeInTheDocument();
    expect(headerStatus(container)).toBe("当前受限 · 预算");
    expect(container.textContent || "").not.toContain("基础配置正常");
  });

  it("最近窗口传达「是否跑通 / 是否降级」但不点名厂商与模型", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      recent_llm: [
        {
          id: "llm-ok", source: "llm_calls", kind: "视频分析", label: null, status: "success",
          finished_at: "2026-07-09T11:58:00Z", has_error: false, masked: false,
          provider: "google", model: "gemini-3.6-flash", fallback_mode: "provider_fallback", fallback_used: true,
        },
        {
          id: "llm-blocked", source: "llm_reservations", kind: "视频分析", label: null, status: "blocked",
          finished_at: "2026-07-09T11:57:00Z", has_error: false, masked: false, reason_code: "parse_failure",
        },
      ],
    }));

    render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("备用通道已跑通")).toBeInTheDocument();
    expect(screen.getByText("模型返回内容无法解析，未写入正式结果。")).toBeInTheDocument();
  });
});

describe("DashboardTaskQueueCard 单链路失败不拖垮整卡", () => {
  it("基础配置读取 403 时其余三个区块照常显示真实数据", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      queued: [task({ id: "q-1", status: "queued", stage: "queued" })],
    }));
    apiFetch.mockImplementation((url: string) => (
      url === "/api/admin/system/models"
        ? Promise.reject(httpError(403))
        : Promise.resolve(costPayload())
    ));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("暂无抓取任务")).toBeInTheDocument();
    // 泳道与成本页脚照常;只有依赖 /system/models 的三格如实说“无权限”。
    expect(within(laneOf("等待中")).getByText("1")).toBeInTheDocument();
    expect(screen.getAllByText("无权限")).toHaveLength(3);
    expect(screen.getByText("可用")).toBeInTheDocument();
    expect(container).toHaveTextContent("今日 3 次 · $0.34");
    expect(headerStatus(container)).toBe("1 等待中");
    expect(container.textContent || "").not.toContain("基础配置正常");
  });

  it("成本账本 500 时只有页脚降级，队列与基础配置不受影响", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload());
    apiFetch.mockImplementation((url: string) => (
      url === "/api/admin/system/models"
        ? Promise.resolve(systemModelsPayload())
        : Promise.reject(httpError(500))
    ));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("今日成本读取失败")).toBeInTheDocument();
    expect(screen.getByText("3/3")).toBeInTheDocument();
    expect(screen.getByText("暂无抓取任务")).toBeInTheDocument();
    expect(container.textContent || "").not.toContain("$0.00");
  });

  it("队列接口失败时给独立错误态，绝不冒充“无任务”，其余两条链路照常", async () => {
    fetchProgressCenter.mockRejectedValue(httpError(500));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);

    expect(await screen.findByText("3/3")).toBeInTheDocument();
    expect(headerStatus(container)).toBe("队列状态读取失败");
    expect(within(laneOf("抓取")).getByText("失败")).toBeInTheDocument();
    expect(container).toHaveTextContent("今日 3 次 · $0.34");
    expect(container.textContent || "").not.toContain("暂无抓取任务");
    expect(container.textContent || "").not.toContain("当前无任务");
  });

  it("队列接口 403 与 503 各自给出可分辨的说明", async () => {
    fetchProgressCenter.mockRejectedValue(httpError(403));
    const first = render(<DashboardTaskQueueCard apiToken="token" />);
    expect(headerStatus((await screen.findAllByText("无权查看队列"))[0].closest("article") as HTMLElement))
      .toBe("无权查看队列");
    first.unmount();

    fetchProgressCenter.mockRejectedValue(httpError(503));
    const second = render(<DashboardTaskQueueCard apiToken="token" />);
    await screen.findAllByText("队列状态暂不可读");
    expect(headerStatus(second.container)).toBe("队列状态暂不可读");
  });
});

describe("DashboardTaskQueueCard 门面文案", () => {
  it("卡标题按用户口径改为「排队」/「任务队列」", async () => {
    const compactView = render(<DashboardTaskQueueCard apiToken="token" compact />);
    expect(await screen.findByText("排队")).toBeInTheDocument();
    compactView.unmount();

    render(<DashboardTaskQueueCard apiToken="token" />);
    expect(await screen.findByText("任务队列")).toBeInTheDocument();
  });

  it("正文、title 与 aria-label 一律不出现厂商名、模型 id 与内部术语", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload({
      running: [task({ id: "r-1", stage: "search", kind: "评论采集", label: "creator-a" })],
      recent_llm: [
        {
          id: "llm-ok", source: "llm_calls", kind: "视频分析", label: null, status: "success",
          finished_at: "2026-07-09T11:58:00Z", has_error: false, masked: false,
          provider: "google", model: "gemini-3.6-flash",
        },
        {
          id: "llm-blocked", source: "llm_calls", kind: "视频分析", label: null, status: "failed",
          finished_at: "2026-07-09T11:57:00Z", has_error: true, masked: false, reason_code: "provider_429",
        },
      ],
    }));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);
    await screen.findByText("评论采集 · creator-a");

    expect(chromeText(container)).not.toMatch(BANNED_ON_CHROME);
  });

  it("紧凑态与错误态同样不泄漏内部术语", async () => {
    fetchProgressCenter.mockRejectedValue(httpError(500));
    apiFetch.mockImplementation(() => Promise.reject(httpError(403)));

    const { container } = render(<DashboardTaskQueueCard apiToken="token" compact />);
    await screen.findAllByText("队列状态读取失败");

    expect(chromeText(container)).not.toMatch(BANNED_ON_CHROME);
  });

  it("去术语后仍保住四项基础判断与诚实说明", async () => {
    fetchProgressCenter.mockResolvedValue(progressPayload());

    const { container } = render(<DashboardTaskQueueCard apiToken="token" />);
    await screen.findByText("暂无抓取任务");

    expect(screen.getByText("服务通道")).toBeInTheDocument();
    expect(screen.getByText("月总预算")).toBeInTheDocument();
    expect(screen.getByText("人工放行")).toBeInTheDocument();
    expect(screen.getByText("正式核验")).toBeInTheDocument();
    expect(screen.getByText("3/3")).toBeInTheDocument();
    expect(screen.getByText("4/4")).toBeInTheDocument();
    expect(screen.getByText("0/4")).toBeInTheDocument();
    expect(screen.getByText("窗口内无成功记录")).toBeInTheDocument();
    expect(screen.getByText("最近记录未见阻断")).toBeInTheDocument();
    const cardTitle = container.querySelector("article")?.getAttribute("title") || "";
    expect(cardTitle).toContain("不代表某个具体任务一定能跑通");
    expect(cardTitle).toContain("每个任务开跑前还会再单独确认一次");
    expect(cardTitle).toContain("近 2 小时最多 5 条记录");
  });
});

describe("DashboardTaskQueueCard 轮询与开销", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    setVisibility("visible");
  });

  function callsTo(url: string): number {
    return apiFetch.mock.calls.filter(([called]: [string]) => String(called).startsWith(url)).length;
  }

  it("基础配置快照每次挂载只取一次，不再每分钟轮询", async () => {
    fetchProgressCenter.mockReset().mockResolvedValue(progressPayload());
    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(callsTo("/api/admin/system/models")).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600_000);
    });
    expect(callsTo("/api/admin/system/models")).toBe(1);
  });

  it("成本账本 5 分钟一拍，页面隐藏期间不发请求", async () => {
    fetchProgressCenter.mockReset().mockResolvedValue(progressPayload());
    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(callsTo("/api/admin/vkpi/ops/cost-ledger")).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(299_999);
    });
    expect(callsTo("/api/admin/vkpi/ops/cost-ledger")).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(callsTo("/api/admin/vkpi/ops/cost-ledger")).toBe(2);

    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_200_000);
    });
    expect(callsTo("/api/admin/vkpi/ops/cost-ledger")).toBe(2);

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(callsTo("/api/admin/vkpi/ops/cost-ledger")).toBe(3);
  });

  it("请求结算后才启动空闲 30 秒计时，慢请求期间不重入", async () => {
    const first = deferred<ReturnType<typeof progressPayload>>();
    fetchProgressCenter
      .mockReset()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(progressPayload());

    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve(progressPayload());
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
  });

  it("存在活跃任务时保持 10 秒一拍，任务空闲后退回 30 秒", async () => {
    fetchProgressCenter
      .mockReset()
      .mockResolvedValueOnce(progressPayload({ running: [task({ id: "r-1" })] }))
      .mockResolvedValue(progressPayload());

    render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_999);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(3);
  });

  it("页面隐藏中止并暂停，恢复只补一拍，卸载也中止当前请求", async () => {
    const signals: AbortSignal[] = [];
    fetchProgressCenter.mockReset().mockImplementation(
      ({ signal }: { signal?: AbortSignal }) => new Promise((_, reject) => {
        if (!signal) throw new Error("missing abort signal");
        signals.push(signal);
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      }),
    );

    const view = render(<DashboardTaskQueueCard apiToken="token" />);
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);
    expect(signals[0].aborted).toBe(false);

    setVisibility("hidden");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(signals[0].aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);
    document.dispatchEvent(new Event("visibilitychange"));
    await flushMicrotasks();
    expect(fetchProgressCenter).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(signals[1].aborted).toBe(true);
  });
});
