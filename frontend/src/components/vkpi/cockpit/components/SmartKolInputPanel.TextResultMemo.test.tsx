// M2「治卡」②:68 props 的结果巨树套 memo 之后,到底挡不挡得住?
// 这里用「巨树内部真的被执行了几次」当口径 —— 探针挂在 SearchEvaluationStatus 一定会调的
// useSearchFeedbackLabeledCount 上,它被调一次 = 巨树重渲一次。
//
// 要挡住的是线上真实形态:controller 每渲染重建回调(run/retrySearchSession/…)+ 每渲染重算
// 派生对象(activeSessionCounts / sessionBanner / llmPlan)。这两类东西引用永远是新的,
// 裸 React.memo 的浅比较一次都挡不住。
import { act, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const bodyRenderProbe = vi.hoisted(() => vi.fn());

vi.mock("../../../../services/vkpi/searchFeedback-api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    useSearchFeedbackLabeledCount: () => {
      bodyRenderProbe();
      return 0;
    },
  };
});

import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";
import { TextResultSection } from "./SmartKolInputPanel.TextResult";
import {
  textResultPropsAreEqual,
  useStableTextResultCallbacks,
  type TextResultSectionProps,
} from "./SmartKolInputPanel.TextResult.memo";
import {
  __resetSearchProgressStoreForTests,
  publishSearchProgressNotice,
} from "./SmartKolInputPanel.progressStore";

function progressFixture(): SearchSessionProgress {
  return {
    phase: "enriching",
    phaseLabel: "后台深析中",
    target: 30,
    basicVisible: 12,
    profileReady: 4,
    profileCompleted: 5,
    profileSucceeded: 4,
    profileFailed: 1,
    profileRemaining: 25,
    currentItem: null,
    deepReady: 0,
    deepPartial: 0,
    failed: 1,
    accounted: 1,
    downstreamTracked: false,
    video: { ready: 0, active: 0, failed: 0, notRequested: 30 },
    comments: { ready: 0, active: 0, failed: 0, notRequested: 30 },
    audience: { ready: 0, active: 0, failed: 0, notRequested: 30 },
    completionContractExplicit: false,
    baseComplete: false,
    requestedTasksTerminal: false,
    fullAnalysisComplete: false,
    decisionEligible: false,
    requiredTasksComplete: false,
    contract: null,
  };
}

function emptyRecall(): TextResultSectionProps["recallResult"] {
  return {
    method: "vector_recall",
    query: {},
    ratio: { creator_quota: 7, reviewer_quota: 3, policy: "soft", mixed_policy: "dominant", dedupe: true },
    items: [],
    buckets: { creator: [], reviewer: [] },
    diagnostics: {},
  } as TextResultSectionProps["recallResult"];
}

/** 真·稳定的 state 引用(useState/useMemo 出来的那种),每次渲染不变。 */
const SHARED = {
  recallResult: emptyRecall(),
  discoveryItems: [] as any[],
  discoveryPlatforms: ["youtube", "instagram", "tiktok"],
  contentLanguages: [] as string[],
  kolProfileTypes: [] as string[],
  pickedIds: new Set<number>(),
  favoriteIds: new Set<number>() as ReadonlySet<number>,
  favoriteBusyIds: new Set<number>() as ReadonlySet<number>,
  favoriteResults: new Map<number, string>() as ReadonlyMap<number, string>,
  favoriteErrors: new Map<number, string>() as ReadonlyMap<number, string>,
};

/** 每次调用都重建回调与派生对象 —— 复刻 controller 现状(它不在本车道名下,不能改)。 */
function propsFixture(overrides: Partial<TextResultSectionProps> = {}): TextResultSectionProps {
  return {
    recallResult: SHARED.recallResult,
    searchSession: null,
    llmPlan: {},
    discoveryItems: SHARED.discoveryItems,
    discoveryTotal: 0,
    discoveryAutoEnrolled: null,
    discoveryBrandExcluded: 0,
    reachFloorDisplay: null,
    input: "35mm f1.2 摄影师",
    apiToken: "tok",
    isBusy: false,
    state: "ready",
    plannerFellBack: false,
    personaEditing: false,
    personaDraft: "",
    setPersonaEditing: () => {},
    setPersonaDraft: () => {},
    setInput: () => {},
    run: () => {},
    discoveryPlatforms: SHARED.discoveryPlatforms,
    setDiscoveryPlatforms: () => {},
    discoveryRegion: "",
    setDiscoveryRegion: () => {},
    contentLanguages: SHARED.contentLanguages,
    setContentLanguages: () => {},
    kolProfileTypes: SHARED.kolProfileTypes,
    setKolProfileTypes: () => {},
    excludeChinese: false,
    setExcludeChinese: () => {},
    queueTextAdvance: () => {},
    pickedIds: SHARED.pickedIds,
    setPickedIds: () => {},
    favNote: "",
    favoriteIds: SHARED.favoriteIds,
    favoriteBusyIds: SHARED.favoriteBusyIds,
    favoriteResults: SHARED.favoriteResults,
    favoriteErrors: SHARED.favoriteErrors,
    favoritesSyncing: false,
    favoritesLoadError: "",
    draftNote: "",
    outreachNote: "",
    outreachResult: null,
    addingFav: false,
    draftBusy: false,
    outreachBusy: false,
    displayedSearchSessionId: 4242,
    isSessionPolling: true,
    isSessionPollPaused: false,
    resultsStale: false,
    approvalReady: false,
    favoriteOne: () => {},
    addPickedToMyKol: () => {},
    approveAndCreateDraft: () => {},
    generateOutreachForPicked: () => {},
    discoveryKey: () => "",
    onOpenRecallItem: () => {},
    // 每渲染重算的派生对象:内容一样,地址每次都新。
    sessionBanner: { tone: "info", label: "正在查找", note: "后台仍在补全" },
    sessionProgress: progressFixture(),
    activeSessionCounts: { ready: 12, executed: 0 },
    sessionPollNotice: "后台查找中...",
    retrySearchSession: () => {},
    resumeSearchPolling: () => {},
    ...overrides,
  };
}

describe("TextResultSection memo", () => {
  beforeEach(() => {
    bodyRenderProbe.mockReset();
    __resetSearchProgressStoreForTests();
  });

  afterEach(() => {
    __resetSearchProgressStoreForTests();
  });

  it("容器重渲但数据没变时,结果巨树一次都不重渲", () => {
    const view = render(<TextResultSection {...propsFixture()} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(1);

    // 五次容器重渲:回调全是新函数、派生对象全是新地址,但内容一模一样。
    for (let i = 0; i < 5; i += 1) view.rerender(<TextResultSection {...propsFixture()} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(1);
  });

  it("真有新数据时照常重渲(memo 不许吞掉真实更新)", () => {
    const view = render(<TextResultSection {...propsFixture()} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(1);

    view.rerender(<TextResultSection {...propsFixture({ activeSessionCounts: { ready: 18, executed: 3 } })} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(2);

    view.rerender(<TextResultSection {...propsFixture({ input: "别的查询" })} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(3);

    view.rerender(<TextResultSection {...propsFixture({ recallResult: emptyRecall() })} />);
    // recallResult 是新对象 → 引用比对判为变化 → 照常重渲(不做深比较,不冒吞更新的险)。
    expect(bodyRenderProbe).toHaveBeenCalledTimes(4);
  });

});

describe("自订阅进度行(M2 ①)", () => {
  beforeEach(() => {
    bodyRenderProbe.mockReset();
    __resetSearchProgressStoreForTests();
  });

  afterEach(() => {
    __resetSearchProgressStoreForTests();
  });

  it("没有实时快照时,进度行照实回落到容器 props(历史会话/终态)", () => {
    render(<TextResultSection {...propsFixture()} />);
    expect(document.body.textContent).toContain("后台查找中...");
    expect(document.body.textContent).toContain("已找到 12");
  });

  it("轮询发布新文案时只重渲这一行,68 props 的结果巨树纹丝不动", () => {
    render(<TextResultSection {...propsFixture()} />);
    expect(bodyRenderProbe).toHaveBeenCalledTimes(1);

    act(() => publishSearchProgressNotice(4242, "阶段：后台深析中 · 基础结果 18/30"));

    // 进度行更新了……
    expect(document.body.textContent).toContain("阶段：后台深析中 · 基础结果 18/30");
    // ……而巨树一次都没有重渲。这就是「2.5 秒一次整页重画」被掐掉的地方。
    expect(bodyRenderProbe).toHaveBeenCalledTimes(1);

    // 计数刻意仍走 props(合并后的真值),不受实时文案影响。
    expect(document.body.textContent).toContain("已找到 12");
  });
});

describe("useStableTextResultCallbacks", () => {
  it("外壳身份跨渲染恒定,但调用时永远转发到最新那个函数(不读过期闭包)", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { result, rerender } = renderHook(
      (props: TextResultSectionProps) => useStableTextResultCallbacks(props),
      { initialProps: propsFixture({ retrySearchSession: first, run: first }) },
    );
    const stable = result.current;
    rerender(propsFixture({ retrySearchSession: second, run: second }));
    // 身份不变 —— memo 的浅比较过得去。
    expect(result.current).toBe(stable);
    expect(result.current.retrySearchSession).toBe(stable.retrySearchSession);
    // 但转发到的是最新的那个。
    stable.retrySearchSession();
    stable.run("q");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(2);
  });

  it("可选回调缺席时静默 no-op,与原来的 onOpenRecallItem?.(…) 行为一致", () => {
    const { result } = renderHook(
      (props: TextResultSectionProps) => useStableTextResultCallbacks(props),
      { initialProps: propsFixture({ onOpenRecallItem: undefined }) },
    );
    expect(() => result.current.onOpenRecallItem?.({} as never)).not.toThrow();
  });
});

describe("textResultPropsAreEqual", () => {
  it("按内容比对的 key 只有那五个;其余一律引用比对", () => {
    const base = propsFixture();
    expect(textResultPropsAreEqual(base, { ...base })).toBe(true);
    // 内容相同、地址不同 → 判等。
    expect(textResultPropsAreEqual(base, { ...base, activeSessionCounts: { ...base.activeSessionCounts } })).toBe(true);
    expect(textResultPropsAreEqual(base, { ...base, sessionBanner: { ...base.sessionBanner! } })).toBe(true);
    expect(textResultPropsAreEqual(base, { ...base, llmPlan: {} })).toBe(true);
    expect(textResultPropsAreEqual(base, { ...base, sessionProgress: progressFixture() })).toBe(true);
    // 内容变了 → 判不等。
    expect(textResultPropsAreEqual(base, { ...base, activeSessionCounts: { ready: 99, executed: 0 } })).toBe(false);
    expect(textResultPropsAreEqual(base, { ...base, sessionBanner: null })).toBe(false);
    // 非白名单 key 换地址 → 一律判不等(不做深比较,不赌)。
    expect(textResultPropsAreEqual(base, { ...base, discoveryItems: [] })).toBe(false);
    expect(textResultPropsAreEqual(base, { ...base, run: () => {} })).toBe(false);
  });
});
