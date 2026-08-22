import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IntelligentQueryAnswer } from "../../../../services/vkpi/intelligent-api";
import { I18nContext, makeT } from "../lib/i18n";
import { I18N_EN } from "../data/i18nEn";
import { AskCommandOverlay } from "./AskCommandOverlay";

const mocks = vi.hoisted(() => ({
  queryIntelligent: vi.fn(),
  fetchSuggestions: vi.fn(),
  globalSearch: vi.fn(),
  catalogSuggest: vi.fn(),
  fetchProgressCenter: vi.fn(),
  listKolSearchHistory: vi.fn(),
}));

vi.mock("../../../../services/vkpi/intelligent-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../../services/vkpi/intelligent-api")>();
  return { ...original, queryIntelligent: mocks.queryIntelligent, fetchSuggestions: mocks.fetchSuggestions };
});

vi.mock("../../../../services/vkpi/globalSearch-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../../../services/vkpi/globalSearch-api")>();
  return { ...original, globalSearch: mocks.globalSearch };
});

vi.mock("../../../../services/vkpi/catalogSuggest-api", () => ({ catalogSuggest: mocks.catalogSuggest }));
vi.mock("../../../../services/vkpi/progressCenter-api", () => ({ fetchProgressCenter: mocks.fetchProgressCenter }));
vi.mock("../../../../services/vkpi/kolPool-api.search", () => ({ listKolSearchHistory: mocks.listKolSearchHistory }));

const EMPTY_PROGRESS = { status: "ready", generated_at: null, counts: { running: 0, queued: 0, active_total: 0, recent_total: 0 }, running: [], queued: [], recent_done: [], recent_llm: [], stage_flow: [], diagnostics: { worker_online: null } };
const EMPTY_CATALOG = { status: "empty", q: "", items: [], source_status: { lens_evidence: { status: "ready", result_count: 0 }, products: { status: "ready", result_count: 0 } } };
const READY_STATUS = { kols: { status: "ready", result_count: 0 }, projects: { status: "ready", result_count: 0 }, events: { status: "ready", result_count: 0 } };

function kol(id: number, name: string) {
  return { id, platform: "YouTube", handle: `@${name.toLowerCase()}`, display_name: name, avatar_url: null, followers: 100 };
}

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
  const t = makeT(lang, I18N_EN);
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
  mocks.catalogSuggest.mockReset().mockResolvedValue(EMPTY_CATALOG);
  mocks.fetchProgressCenter.mockReset().mockResolvedValue(EMPTY_PROGRESS);
  mocks.listKolSearchHistory.mockReset().mockResolvedValue({ status: "ready", items: [] });
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
  it("建议区接 /intelligent/suggestions;失败退回本地四条并标「离线建议」", async () => {
    mocks.fetchSuggestions.mockResolvedValue(["本周谁的视频涨粉最快？", "哪些 KOL 还没回复？"]);
    const first = renderOverlay();
    expect(await screen.findByRole("option", { name: /本周谁的视频涨粉最快/ })).toBeInTheDocument();
    expect(screen.queryByText("离线建议")).not.toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
    expect(screen.getByText("最近")).toBeInTheDocument();
    expect(screen.getByText("建议")).toBeInTheDocument();
    first.unmount();

    mocks.fetchSuggestions.mockRejectedValue(new Error("boom"));
    renderOverlay();
    expect(await screen.findByRole("option", { name: /目前 KOL 数量是多少/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /总结本周市场对于 Viltrox 的评价/ })).toBeInTheDocument();
    expect(screen.getByText("离线建议")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "近30天各地区的表现怎么样?" })).not.toBeInTheDocument();
  });

  it("首屏三区:进行中接进度中心(本人可见、≤3 条)、最近合并本机留痕与找达人记录、来源失败诚实标注", async () => {
    mocks.fetchProgressCenter.mockResolvedValue({
      ...EMPTY_PROGRESS,
      running: [
        { id: "j1", label: "Alice 视频深析", kind: "video深析", status: "running", stage_label: "分析", progress_pct: 40, kol_pool_id: "9", masked: false },
        { id: "j2", label: "他人任务", kind: "video深析", status: "running", stage_label: "分析", progress_pct: 10, kol_pool_id: null, masked: true },
        { id: "j3", label: "任务三", status: "running", masked: false },
        { id: "j4", label: "任务四", status: "running", masked: false },
        { id: "j5", label: "任务五", status: "running", masked: false },
      ],
    });
    mocks.listKolSearchHistory.mockResolvedValue({ status: "ready", items: [{ id: 77, query_text: "sony 85mm 人像", query_type: "text_recall" }] });
    window.localStorage.setItem("vkpi:ask-recent", JSON.stringify([
      { kind: "nav", id: "nav:dealers", label: "经销商", detail: "板块", action: { type: "navigate", route: "dealers" }, at: 1 },
    ]));
    const sessionEvent = vi.fn();
    window.addEventListener("vkpi:open-kol-search-session", sessionEvent);
    const { onClose } = renderOverlay();

    expect(await screen.findByRole("option", { name: /Alice 视频深析/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /他人任务/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /任务五/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /经销商/ })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("option", { name: /sony 85mm 人像/ }));
    expect(sessionEvent).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem("vkpi:pendingKolSearchSessionId")).toBe("77");
    expect(onClose).toHaveBeenCalled();
    window.removeEventListener("vkpi:open-kol-search-session", sessionEvent);
  });

  it("进度中心不可用时进行中区标注「该来源暂不可用」而不是空", async () => {
    mocks.fetchProgressCenter.mockRejectedValue(new Error("down"));
    renderOverlay();
    expect(await screen.findAllByText("该来源暂不可用")).not.toHaveLength(0);
  });

  it.each([
    ["@", "alice", "kol"],
    ["＠", "alice", "kol"],
    ["#", "26mm", "project"],
    ["＃", "26mm", "project"],
  ])("前缀 %s 只请求 /global-search 并只保留对应实体", async (prefix, term, kind) => {
    mocks.globalSearch.mockResolvedValue({
      kols: [kol(9, "Alice")],
      projects: [{ id: 33, project_uid: "P-33", project_name: "26mm EVO", stage: "planning", stage_status: null, platform: null }],
      events: [{ id: "evt-1", title: "26mm Expo", status: null, start_date: "2026-09-01", end_date: null }],
      source_status: READY_STATUS,
    });
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: `${prefix}${term}` } });
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledWith(term, expect.anything()));
    expect(mocks.catalogSuggest).not.toHaveBeenCalled();
    if (kind === "kol") {
      expect(await screen.findByRole("option", { name: /Alice/ })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: /26mm EVO/ })).not.toBeInTheDocument();
    } else {
      expect(await screen.findByRole("option", { name: /26mm EVO/ })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: /26mm Expo/ })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: /Alice/ })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /让 V-KPI 回答这个问题/ })).not.toBeInTheDocument();
  });

  it.each(["$", "＄"])("前缀 %s 走 /catalog/suggest,Enter 打开 SKU 360°", async (prefix) => {
    mocks.catalogSuggest.mockResolvedValue({
      status: "ready", q: "85",
      items: [
        { sku: "", display_name: "AF 85mm F1.4 Pro", lens_key: "af85mmf14pro" },
        { sku: "AF-85MM-F14-PRO-FE", display_name: "AF 85mm F1.4 Pro Full-Frame Lens for Sony E-Mount", lens_key: "" },
      ],
      source_status: { lens_evidence: { status: "ready", result_count: 1 }, products: { status: "ready", result_count: 1 } },
    });
    const skuEvent = vi.fn();
    window.addEventListener("vkpi:open-sku360", skuEvent);
    const { onNavigate, onClose } = renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: `${prefix}85` } });
    await waitFor(() => expect(mocks.catalogSuggest).toHaveBeenCalledWith("85", expect.objectContaining({ limit: 20 })));
    expect(mocks.globalSearch).not.toHaveBeenCalled();
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["AF 85mm F1.4 Pro镜头系列", "AF 85mm F1.4 Pro Full-Frame Lens for Sony E-MountAF-85MM-F14-PRO-FE"]);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(window.sessionStorage.getItem("vkpi:sku360-sku")).toBe("AF-85MM-F14-PRO-FE");
    expect(skuEvent).toHaveBeenCalledTimes(1);
    expect(onNavigate).toHaveBeenCalledWith("sku360");
    expect(onClose).toHaveBeenCalled();
    window.removeEventListener("vkpi:open-sku360", skuEvent);

    // 家族名(无单一 SKU):预填 SKU 360° 搜索词而不是假装选中某个 SKU。
    fireEvent.click(screen.getAllByRole("option")[0]);
    expect(window.sessionStorage.getItem("vkpi:sku360-search")).toBe("AF 85mm F1.4 Pro");
    expect(window.sessionStorage.getItem("vkpi:sku360-sku")).toBe("AF-85MM-F14-PRO-FE");
  });

  it.each(["/", "／"])("前缀 %s 本地匹配板块(中英 label + 别名),零网络", async (prefix) => {
    const { onNavigate, onClose } = renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: `${prefix}经销商地图` } });
    expect(await screen.findByRole("option", { name: /经销商/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(input, { target: { value: `${prefix}dealer map` } });
    expect(await screen.findByRole("option", { name: /经销商/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.change(input, { target: { value: `${prefix}问数` } });
    expect(await screen.findByRole("option", { name: /问数/ })).toBeInTheDocument();
    fireEvent.change(input, { target: { value: `${prefix}我的 KOL` } });
    const myKol = await screen.findByRole("option", { name: /我的 KOL/ });
    expect(myKol).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("my-kol");
    expect(onClose).toHaveBeenCalled();
    await new Promise((resolve) => setTimeout(resolve, 260));
    expect(mocks.globalSearch).not.toHaveBeenCalled();
    expect(mocks.catalogSuggest).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem("vkpi:ask-recent") || "[]")[0]).toMatchObject({ kind: "nav", id: "nav:my-kol" });
  });

  it("无前缀=混合:先本地板块直达,防抖后并行 /global-search + /catalog/suggest", async () => {
    mocks.globalSearch.mockResolvedValue({ kols: [kol(9, "Dealer Dan")], projects: [], events: [], source_status: READY_STATUS });
    mocks.catalogSuggest.mockResolvedValue({ ...EMPTY_CATALOG, status: "ready", items: [{ sku: "DC-A1", display_name: "DC-A1 Dealer Kit", lens_key: "" }] });
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "dealer" } });
    expect(await screen.findByRole("option", { name: /经销商/ })).toBeInTheDocument();
    expect(mocks.globalSearch).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledWith("dealer", expect.anything()));
    await waitFor(() => expect(mocks.catalogSuggest).toHaveBeenCalledWith("dealer", expect.anything()));
    expect(await screen.findByRole("option", { name: /Dealer Dan/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /DC-A1 Dealer Kit/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /让 V-KPI 回答这个问题/ })).toBeInTheDocument();
  });

  it("键盘流:Tab 在 候选|问 AI|答案 三区循环,候选与答案同屏,Esc 逐层回退,无打字机", async () => {
    mocks.globalSearch.mockResolvedValue({ kols: [kol(9, "Alice")], projects: [], events: [], source_status: READY_STATUS });
    mocks.queryIntelligent.mockResolvedValue(answer({
      answer: "当前可见范围共有 1,594 位 KOL。",
      actions: [
        { type: "navigate", label: "打开 KOL Pool", route: "kol-pool", requires_approval: false },
        { type: "suggest_query", label: "再问一个", params: { query: "多少 KOL 做过 26mm EVO 视频？" }, requires_approval: false },
      ],
    }));
    const { onNavigate, onClose } = renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    fireEvent.change(input, { target: { value: "alice" } });
    const alice = await screen.findByRole("option", { name: /Alice/ });
    expect(alice).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getByRole("button", { name: /让 V-KPI 回答这个问题/ }).className).toContain("is-zone-active");
    expect(alice).toHaveAttribute("aria-selected", "false");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("当前可见范围共有 1,594 位 KOL。")).toBeInTheDocument();
    expect(document.querySelector(".vkpi-ask-dialog__answer p i")).toBeNull();
    expect(screen.getByRole("option", { name: /Alice/ })).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-activedescendant", "vkpi-ask-action-0");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute("aria-activedescendant", "vkpi-ask-action-1");
    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getByRole("option", { name: /Alice/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Tab" });
    fireEvent.keyDown(input, { key: "Tab" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(input).toHaveAttribute("aria-activedescendant", "vkpi-ask-action-0");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("kol-pool");
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByText("当前可见范围共有 1,594 位 KOL。")).not.toBeInTheDocument();
    expect(input).toHaveValue("alice");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(input).toHaveValue("");
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("无候选时 Enter 直接问 AI;建议 chip 用 Enter 发问并留痕到最近", async () => {
    mocks.queryIntelligent.mockResolvedValue(answer());
    renderOverlay();
    const input = screen.getByRole("combobox", { name: "智能问答与全局搜索" });
    const chip = await screen.findByRole("option", { name: /目前 KOL 数量是多少/ });
    expect(chip).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledWith("token-1", "目前 KOL 数量是多少？", expect.anything()));
    expect(JSON.parse(window.localStorage.getItem("vkpi:ask-recent") || "[]")[0]).toMatchObject({ kind: "suggestion", label: "目前 KOL 数量是多少？" });

    fireEvent.change(input, { target: { value: "自由提问一句" } });
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledTimes(1));
    await screen.findByText("没有匹配的 @KOL / #项目 / $SKU");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.queryIntelligent).toHaveBeenCalledTimes(2));
    expect(mocks.queryIntelligent.mock.calls[1][1]).toBe("自由提问一句");
  });

  it.each([
    ["none", "没有结果", { kols: { status: "ready", result_count: 0 }, projects: { status: "ready", result_count: 0 }, events: { status: "ready", result_count: 0 } }, "没有匹配的 @KOL / #项目 / $SKU", "按 Tab 切到问 AI"],
    ["none(前缀)", "#没有结果", { kols: { status: "error", result_count: 0 }, projects: { status: "ready", result_count: 0 }, events: { status: "ready", result_count: 0 } }, "没有匹配的 @KOL / #项目 / $SKU", "换个前缀或关键词试试"],
    ["unavailable", "@没有结果", { kols: { status: "error", result_count: 0, reason: "query_and_fallback_failed" }, projects: { status: "ready", result_count: 0 }, events: { status: "ready", result_count: 0 } }, "该来源暂不可用", "这不是零结果，稍后重试或改用其他前缀"],
    ["scope", "没有结果", { kols: { status: "blocked", result_count: 0 }, projects: { status: "blocked", result_count: 0 }, events: { status: "blocked", result_count: 0 } }, "你的范围内没有", "当前账号可见范围内没有匹配；管理员可扩权后再试"],
  ])("诚实空态三态 %s:回填永不计入命中", async (_kind, typed, sourceStatus, title, body) => {
    mocks.globalSearch.mockResolvedValue({ kols: [], projects: [], events: [], source_status: sourceStatus });
    renderOverlay();
    fireEvent.change(screen.getByRole("combobox", { name: "智能问答与全局搜索" }), { target: { value: typed } });
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText(body)).toBeInTheDocument();
    expect(screen.queryByRole("option")).not.toBeInTheDocument();
  });

  it("来源被权限拒绝(403)记为范围空态而非零结果", async () => {
    mocks.globalSearch.mockRejectedValue(Object.assign(new Error("forbidden"), { status: 403 }));
    renderOverlay();
    fireEvent.change(screen.getByRole("combobox", { name: "智能问答与全局搜索" }), { target: { value: "@zoe" } });
    expect(await screen.findByText("你的范围内没有")).toBeInTheDocument();
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

    expect(await screen.findByText("该来源暂不可用")).toBeInTheDocument();
    expect(screen.queryByText("没有匹配的 @KOL / #项目 / $SKU")).not.toBeInTheDocument();
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
