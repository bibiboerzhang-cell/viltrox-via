import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IntelligentQueryAnswer } from "../../../../services/vkpi/intelligent-api";
import { I18nContext, makeT } from "../lib/i18n";
import { AskCommandOverlay } from "./AskCommandOverlay";

const mocks = vi.hoisted(() => ({
  queryIntelligent: vi.fn(),
  fetchSuggestions: vi.fn(),
  globalSearch: vi.fn(),
}));

vi.mock("../../../../services/vkpi/intelligent-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../../services/vkpi/intelligent-api")>();
  return { ...original, queryIntelligent: mocks.queryIntelligent, fetchSuggestions: mocks.fetchSuggestions };
});

vi.mock("../../../../services/vkpi/globalSearch-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../../services/vkpi/globalSearch-api")>();
  return { ...original, globalSearch: mocks.globalSearch };
});

function answer(overrides: Partial<IntelligentQueryAnswer> = {}): IntelligentQueryAnswer {
  return {
    schema_version: "ask_find_v2",
    request_id: "req-1",
    status: "ready",
    intent: "kol.pool.overview",
    answer: "当前可见范围共有 1,594 位 KOL。",
    facts: [],
    evidence: [],
    coverage: { status: "unknown", matched_entities: 0, evidence_count: 0, notes: [] },
    freshness: { status: "unknown", generated_at: "", timezone: "UTC" },
    missing_fields: [],
    actions: [],
    trace: {
      request_id: "req-1",
      client_request_id: "client-1",
      thread_id: "ask-find-topbar",
      scope: { mode: "own" },
      mode: "deterministic",
      deterministic: true,
      query_version: "ask_find_v2",
      took_ms: 23,
    },
    mode: "intent",
    cached: false,
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function renderOverlay({ lang = "zh", onClose = vi.fn(), onNavigate = vi.fn() } = {}) {
  const t = makeT(lang);
  return {
    onClose,
    onNavigate,
    ...render(
      <I18nContext.Provider value={{ t, lang, setLang: vi.fn() }}>
        <AskCommandOverlay open apiToken="token-1" onClose={onClose} onNavigate={onNavigate} />
      </I18nContext.Provider>,
    ),
  };
}

beforeEach(() => {
  window.localStorage.clear();
  mocks.queryIntelligent.mockReset();
  mocks.fetchSuggestions.mockReset().mockResolvedValue([]);
  mocks.globalSearch.mockReset().mockResolvedValue({ kols: [], projects: [], events: [] });
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
});

describe("AskCommandOverlay Ask & Find v2", () => {
  it("空态只展示 v2 已支持的四类问题", () => {
    renderOverlay();

    expect(screen.getByRole("button", { name: "目前 KOL 数量是多少？" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "多少 KOL 做过 26mm EVO 视频？" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "搜索 26mm EVO 项目" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "总结本周市场对于 Viltrox 的评价" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "近30天各地区的表现怎么样?" })).not.toBeInTheDocument();
  });

  it("输入时保留即时搜索，并可用上下键选择、Enter 打开实体", async () => {
    mocks.globalSearch.mockResolvedValue({
      kols: [{ id: 9, platform: "YouTube", handle: "@alice", display_name: "Alice", avatar_url: null, followers: 100 }],
      projects: [{ id: 33, project_uid: "P-33", project_name: "26mm EVO", stage: "planning", stage_status: null, platform: null }],
      events: [],
      source_status: {
        kols: { status: "ready", result_count: 1 },
        projects: { status: "ready", result_count: 1 },
        events: { status: "ready", result_count: 0 },
      },
    });
    const projectEvent = vi.fn();
    window.addEventListener("vkpi:open-project-task", projectEvent);
    renderOverlay();

    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "26mm" } });
    expect(await screen.findByRole("option", { name: /Alice/ })).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: /26mm EVO/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });

    expect(projectEvent).toHaveBeenCalledTimes(1);
    expect((projectEvent.mock.calls[0][0] as CustomEvent).detail).toEqual({ projectId: "33" });
    expect(mocks.queryIntelligent).not.toHaveBeenCalled();
    window.removeEventListener("vkpi:open-project-task", projectEvent);
  });

  it("取消旧问答请求并阻止旧响应覆盖新问题", async () => {
    const first = deferred<IntelligentQueryAnswer>();
    const second = deferred<IntelligentQueryAnswer>();
    mocks.queryIntelligent.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });

    fireEvent.change(input, { target: { value: "第一个问题" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledTimes(1));
    const firstSignal = mocks.queryIntelligent.mock.calls[0][2].signal as AbortSignal;

    fireEvent.change(input, { target: { value: "第二个问题" } });
    expect(firstSignal.aborted).toBe(true);
    fireEvent.keyDown(input, { key: "Enter", metaKey: true });
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledTimes(2));

    second.resolve(answer({ answer: "新答案" }));
    expect(await screen.findByText("新答案")).toBeInTheDocument();
    first.resolve(answer({ answer: "不应出现的旧答案" }));
    await Promise.resolve();
    expect(screen.queryByText("不应出现的旧答案")).not.toBeInTheDocument();
    expect(screen.getByText("新答案")).toBeInTheDocument();
  });

  it("输入变化会取消旧搜索并阻止旧候选覆盖新候选", async () => {
    const first = deferred<any>();
    const second = deferred<any>();
    mocks.globalSearch.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });

    fireEvent.change(input, { target: { value: "Alice" } });
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledTimes(1));
    const firstSignal = mocks.globalSearch.mock.calls[0][1].signal as AbortSignal;
    fireEvent.change(input, { target: { value: "Bob" } });
    expect(firstSignal.aborted).toBe(true);
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledTimes(2));

    second.resolve({
      kols: [{ id: 2, platform: "YouTube", handle: "@bob", display_name: "Bob", avatar_url: null, followers: 20 }],
      projects: [], events: [],
    });
    expect(await screen.findByRole("option", { name: /Bob/ })).toBeInTheDocument();
    first.resolve({
      kols: [{ id: 1, platform: "YouTube", handle: "@alice", display_name: "Alice", avatar_url: null, followers: 10 }],
      projects: [], events: [],
    });
    await Promise.resolve();
    expect(screen.queryByRole("option", { name: /Alice/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Bob/ })).toBeInTheDocument();
  });

  it("用结构化卡片展示数字、覆盖、时间、缺口和来源，不输出原始 JSON", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      status: "partial",
      answer: "确认命中以视频证据为准。",
      intent: "kol.video_topic.count",
      facts: [{ key: "confirmed_kols", label: "确认做过", value: 18, value_type: "integer", unit: "位", basis: "标题或字幕明确命中", confidence: "high" }],
      coverage: { status: "partial", matched_entities: 18, evidence_count: 27, total_scope: 1594, analyzed_count: 16, ratio: 0.593, notes: ["11条待分析"] },
      freshness: { status: "fresh", generated_at: "2026-08-04T12:00:00Z", data_updated_at: "2026-08-04T11:45:00Z", timezone: "UTC" },
      missing_fields: [{ field: "完整视频", reason: "11条尚未完成全片分析", impact: "不能计入确认数量" }],
      evidence: [{ id: "ev-1", kind: "video", source: "YouTube", title: "26mm EVO review", snippet: "字幕明确提及产品", observed_at: "2026-08-03", confidence: "high" }],
    }));
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "多少 KOL 做过 26mm EVO 视频" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("确认命中以视频证据为准。")).toBeInTheDocument();
    expect(screen.getByText("结果不完整")).toBeInTheDocument();
    expect(screen.getByText("确认做过")).toBeInTheDocument();
    expect(screen.getByText("18 位")).toBeInTheDocument();
    expect(screen.getByText(/18 个匹配/)).toBeInTheDocument();
    expect(screen.getByText("完整视频")).toBeInTheDocument();
    fireEvent.click(screen.getByText(/来源与证据/));
    expect(screen.getByText("26mm EVO review")).toBeInTheDocument();
    expect(screen.getByText("字幕明确提及产品")).toBeInTheDocument();
    expect(document.querySelector(".vkpi-ask-dialog pre")).toBeNull();
  });

  it("把降级与真实完成分开标识", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      status: "degraded",
      answer: "只返回已验证的部分结果。",
      degraded_reason: "source_timeout",
      mode: "degraded",
    }));
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "总结本周市场" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("部分数据源不可用")).toBeInTheDocument();
    expect(screen.getByText("部分数据源暂不可用，以下内容可能不完整。")).toBeInTheDocument();
    expect(screen.queryByText("基于内部真实数据")).not.toBeInTheDocument();
  });

  it("blocked 状态与待审批提案只展示，不执行也不导航", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      status: "blocked",
      answer: "当前范围不可访问。",
      actions: [{ type: "propose_analysis", label: "申请完整视频分析", route: "action-inbox", requires_approval: true }],
    }));
    const { onNavigate } = renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "深度分析" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("数据访问受限")).toBeInTheDocument();
    expect(screen.getByText("申请完整视频分析")).toBeInTheDocument();
    expect(screen.getByText("待人工审批的提案")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /申请完整视频分析/ })).not.toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("只有无需审批的 navigate action 会直接导航", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      actions: [{ type: "navigate", label: "打开 KOL Pool", route: "kol-pool", params: { query: "26mm EVO" }, requires_approval: false }],
    }));
    const searchEvent = vi.fn();
    window.addEventListener("vkpi:open-kol-pool-search", searchEvent);
    const { onNavigate, onClose } = renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "目前 KOL 数量" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    fireEvent.click(await screen.findByRole("button", { name: /打开 KOL Pool/ }));

    expect(onNavigate).toHaveBeenCalledWith("kol-pool");
    expect(onClose).toHaveBeenCalled();
    expect(window.localStorage.getItem("vkpi:pending-kolpool-search")).toBe("26mm EVO");
    expect(searchEvent).toHaveBeenCalledTimes(1);
    window.removeEventListener("vkpi:open-kol-pool-search", searchEvent);
  });

  it("把权限拒绝显示为权限状态，不泄露原始服务错误", async () => {
    mocks.queryIntelligent.mockRejectedValue(Object.assign(new Error("internal_acl_table_denied"), { status: 403 }));
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "查看其他人的项目" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("权限不足")).toBeInTheDocument();
    expect(screen.getByText("你没有查看这部分数据的权限")).toBeInTheDocument();
    expect(screen.queryByText("internal_acl_table_denied")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("把确定的空答案显示为无匹配数据，而不是故障或完成", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      status: "empty",
      answer: "当前可见范围内没有匹配记录。",
      facts: [],
      evidence: [],
      coverage: { status: "empty", matched_entities: 0, evidence_count: 0, notes: [] },
    }));
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "不存在的项目" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("没有匹配数据")).toBeInTheDocument();
    expect(screen.queryByText("查询失败")).not.toBeInTheDocument();
  });

  it("待澄清答案可直接运行后端给出的安全建议问题", async () => {
    mocks.queryIntelligent
      .mockResolvedValueOnce(answer({
        status: "needs_clarification",
        intent: "unknown",
        answer: "请补充要查的对象。",
        actions: [{ type: "suggest_query", label: "目前 KOL 数量是多少？", params: { query: "目前 KOL 数量是多少？" }, requires_approval: false }],
      }))
      .mockResolvedValueOnce(answer());
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "帮我看看" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("需要补充条件")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /目前 KOL 数量是多少/ }));
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledTimes(2));
    expect(mocks.queryIntelligent.mock.calls[1][1]).toBe("目前 KOL 数量是多少？");
  });

  it("区分真实零结果与来源故障", async () => {
    mocks.globalSearch.mockResolvedValue({
      kols: [], projects: [], events: [],
      source_status: {
        kols: { status: "error", result_count: 0, reason: "query_and_fallback_failed" },
        projects: { status: "ready", result_count: 0 },
        events: { status: "ready", result_count: 0 },
      },
    });
    renderOverlay();
    fireEvent.change(screen.getByRole("combobox", { name: "智能问答与全局搜索" }), { target: { value: "没有结果" } });

    expect(await screen.findByText("部分搜索来源暂不可用")).toBeInTheDocument();
    expect(screen.queryByText("没有找到匹配的KOL、项目或活动")).not.toBeInTheDocument();
  });

  it("来源缺失时不把未知证据覆盖显示成真实零", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer({
      status: "partial",
      answer: "当前可核验 18 位 KOL，视频证据源不可用。",
      coverage: { status: "partial", matched_entities: 18, evidence_count: 0, notes: [] },
      trace: {
        ...answer().trace,
        source_status: { videos: { status: "absent", reason: "table_missing" } },
      },
    }));
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "多少 KOL 有视频证据？" } });
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

    expect(await screen.findByText("18 个匹配")).toBeInTheDocument();
    expect(screen.queryByText(/0 条证据/)).not.toBeInTheDocument();
  });

  it("英语模式覆盖入口、占位和问答动作", async () => {
    renderOverlay({ lang: "en" });
    expect(screen.getByRole("dialog", { name: "V-KPI Ask & Find" })).toBeInTheDocument();
    const input = screen.getByPlaceholderText("Ask about the market, KOLs or projects, or search directly");
    await waitFor(() => expect(input).toHaveFocus());
    fireEvent.change(input, { target: { value: "How many KOLs are available?" } });
    expect(screen.getByRole("button", { name: /Ask V-KPI/ })).toBeInTheDocument();
  });
});
