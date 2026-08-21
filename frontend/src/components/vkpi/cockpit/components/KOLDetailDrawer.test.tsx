import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, within, waitFor, fireEvent } from "@testing-library/react";

// P2-7 render smoke(jsdom):KOLDetailDrawer 的「长期记忆」(kolMemory)区。
// mock seam:framer-motion(motion→div)、kolPool-api(4 fn resolve 空)、kolMemory-api(getKolMemory 可控)。
// 红线指纹:kolMemory 区绝不渲染任何 v6_fit/viltrox 数值——传 item.v6_fit=88,断言「长期记忆」section 内不含 88。

vi.mock("framer-motion", () => {
  const React = require("react");
  // 组件身份必须按 tag 缓存:原实现每次访问 motion.div 都造新函数 → React 视为不同组件类型,
  // 每次重渲染整树卸载重挂,任何异步 setState 都会让刚 findBy 到的节点脱离 document(假红)。
  const cache: Record<string, unknown> = {};
  const motionProxy = new Proxy(
    {},
    {
      get: (_target, key: string) => {
        if (!cache[key]) {
          cache[key] = (props: Record<string, unknown>) =>
            React.createElement("div", props, props.children as React.ReactNode);
        }
        return cache[key];
      },
    },
  );
  // LazyMotion 迁移:代码用 m.*(等价 motion.*,共享同一 tag 缓存)+ LazyMotion/domMax。
  return {
    motion: motionProxy,
    m: motionProxy,
    LazyMotion: ({ children }: { children: React.ReactNode }) => children,
    domMax: {},
    domAnimation: {},
    AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
  };
});

const getKolPoolDimensions11 = vi.fn();
const getKolPoolLlmDeepAnalysis = vi.fn();
const getKolPoolContentFit = vi.fn();
const analyzeKolPoolContentFit = vi.fn();
const enqueueVideoAnalysis = vi.fn();
const getKolPoolAccountDossier = vi.fn();
const enqueueAllKolVideos = vi.fn();
const getKolCooperation = vi.fn();
const recordKolCooperation = vi.fn();
const promoteKolPoolToMain = vi.fn();
// Profile 深爬只允许显式按钮触发；mock 用于验证挂载零 provider、点击后走安全队列。
const enqueueKolProfileCrawl = vi.fn();
const refreshAudienceStats = vi.fn();
const getKolVideoAnalysisCache = vi.fn();
const getKolVideoAnalysisBatch = vi.fn();
const revealKolPoolContact = vi.fn();
vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolPoolDimensions11: (...a: unknown[]) => getKolPoolDimensions11(...a),
  getKolPoolLlmDeepAnalysis: (...a: unknown[]) => getKolPoolLlmDeepAnalysis(...a),
  getKolPoolContentFit: (...a: unknown[]) => getKolPoolContentFit(...a),
  analyzeKolPoolContentFit: (...a: unknown[]) => analyzeKolPoolContentFit(...a),
  enqueueVideoAnalysis: (...a: unknown[]) => enqueueVideoAnalysis(...a),
  getKolPoolAccountDossier: (...a: unknown[]) => getKolPoolAccountDossier(...a),
  enqueueAllKolVideos: (...a: unknown[]) => enqueueAllKolVideos(...a),
  getKolCooperation: (...a: unknown[]) => getKolCooperation(...a),
  recordKolCooperation: (...a: unknown[]) => recordKolCooperation(...a),
  promoteKolPoolToMain: (...a: unknown[]) => promoteKolPoolToMain(...a),
  enqueueKolProfileCrawl: (...a: unknown[]) => enqueueKolProfileCrawl(...a),
  refreshAudienceStats: (...a: unknown[]) => refreshAudienceStats(...a),
  getKolVideoAnalysisCache: (...a: unknown[]) => getKolVideoAnalysisCache(...a),
  getKolVideoAnalysisBatch: (...a: unknown[]) => getKolVideoAnalysisBatch(...a),
  revealKolPoolContact: (...a: unknown[]) => revealKolPoolContact(...a),
}));

const getMyKolViewerContext = vi.fn();
const releaseKolClaim = vi.fn();
vi.mock("../../../../services/vkpi/kol-api", () => ({
  getMyKolViewerContext: (...a: unknown[]) => getMyKolViewerContext(...a),
  releaseKolClaim: (...a: unknown[]) => releaseKolClaim(...a),
}));

const getKolMemory = vi.fn();
vi.mock("../../../../services/vkpi/kolMemory-api", () => ({
  getKolMemory: (...a: unknown[]) => getKolMemory(...a),
}));

import { KOLDetailDrawer } from "./KOLDetailDrawer";
import { KOLDrawerGeoDistribution } from "./KOLDetailDrawerSections.More";

beforeEach(() => {
  getKolPoolDimensions11.mockReset().mockResolvedValue({ status: "missing" });
  getKolPoolLlmDeepAnalysis.mockReset().mockResolvedValue({ status: "missing" });
  getKolPoolContentFit.mockReset().mockResolvedValue({ status: "missing" });
  analyzeKolPoolContentFit.mockReset().mockResolvedValue({ status: "queued" });
  getMyKolViewerContext.mockReset().mockResolvedValue({
    paid_actions: { can_run_paid_actions: true, reason: "owned_favorite" },
  });
  releaseKolClaim.mockReset().mockResolvedValue({ status: "released" });
  enqueueVideoAnalysis.mockReset().mockResolvedValue({ status: "queued" });
  getKolPoolAccountDossier.mockReset().mockResolvedValue({ status: "missing" });
  enqueueAllKolVideos.mockReset().mockResolvedValue({ status: "queued" });
  getKolCooperation.mockReset().mockResolvedValue(null);
  recordKolCooperation.mockReset().mockResolvedValue({ status: "" });
  promoteKolPoolToMain.mockReset().mockResolvedValue({ ok: true });
  enqueueKolProfileCrawl.mockReset().mockResolvedValue({ status: "queued" });
  refreshAudienceStats.mockReset().mockResolvedValue({ status: "ok" });
  getKolVideoAnalysisCache.mockReset().mockResolvedValue({ state: "missing" });
  getKolVideoAnalysisBatch.mockReset().mockResolvedValue({ count: 0, items: [] });
  revealKolPoolContact.mockReset().mockResolvedValue({
    status: "empty",
    kol_pool_id: 42,
    contact_masked: false,
    contacts: [],
  });
  getKolMemory.mockReset().mockResolvedValue(null);
  // C1 tab 记忆落 localStorage:清掉,让每条用例都从默认「概览」出发(需要合作 tab 的用例自行点击)。
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

// 【C1 Tab 化】长期记忆区已归「合作」tab(默认 tab 是「概览」),先点 tab 再断言。
function openCoopTab() {
  fireEvent.click(screen.getByText("合作"));
}

const baseItem = {
  id: 42,
  handle: "@frank",
  display_name: "Frank Trades",
  v6_fit: 88, // 红线诱饵:不得渲染进 kolMemory 区
};

const readyMemory = {
  status: "ready",
  snapshot: {
    content_style: "硬核镜头测评 · 实拍对比",
    recommended_product_lines: ["镜头", "云台"],
    risk: { risk_flags: ["曾合作友商"], final_verdict: "可控" },
    fulfillment: { assigned_count: 3, shipped_count: 2, published_count: 1, failed_jobs_count: 0 },
    timeline: [{ event_type: "published", occurred_at: "2026-06-01" }],
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

function renderDrawer(props: Record<string, unknown> = {}) {
  return render(
    <KOLDetailDrawer
      item={baseItem}
      apiToken="tok"
      onClose={() => {}}
      inMyList={false}
      onToggleMyList={() => {}}
      onContact={() => {}}
      {...props}
    />,
  );
}

describe("KOLDetailDrawer 长期记忆区 render smoke", () => {
  it("档案瘦且无证据时挂载不自动入队，显式点击才提交规范主页", async () => {
    renderDrawer({
      item: {
        ...baseItem,
        profile_url: "https://www.youtube.com/@frank",
        posts_count: 0,
        avg_views: 0,
      },
      detailBundle: {
        status: "ready",
        item: { video_evidence: [] },
        video_analysis: { items: [], summary: { ready_count: 0 } },
      },
    });

    await act(async () => { await Promise.resolve(); });
    expect(enqueueKolProfileCrawl).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "KOL深度分析理解(最近20条)" }));

    await waitFor(() => expect(enqueueKolProfileCrawl).toHaveBeenCalledWith(
      "tok",
      42,
      "https://www.youtube.com/@frank",
    ));
  });

  it("深度分析页先展示可信度、证据覆盖和阻断声明", async () => {
    renderDrawer({
      item: { ...baseItem, posts_count: 4, avg_views: 1000 },
      detailBundle: {
        status: "ready",
        analysis_readiness: {
          level: "insufficient",
          status: "blocked",
          claim_status: "descriptive_only",
          abstain: true,
          key_sample_count: 1,
          evidence_coverage: { video_total: 4, deep_ready: 1, deep_ratio: 0.25, full_video_proven: 0 },
          blocking_gaps: [{ code: "key_sample_shortfall", severity: "high", message: "关键样本不足" }],
        },
        video_analysis: { items: [], summary: { evidence_count: 4, ready_count: 1 } },
      },
    });

    await act(async () => {
      fireEvent.click(screen.getByText("深度分析"));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("kol-analysis-trust-panel")).toBeInTheDocument();
    expect(screen.getByText("1/4 · 25%")).toBeInTheDocument();
    expect(screen.getByText("暂不建议下结论")).toBeInTheDocument();
    expect(screen.getByText(/关键样本不足/)).toBeInTheDocument();
    expect(screen.getByText(/不是预测准确率/)).toBeInTheDocument();
  });

  it("按智能搜索解析的精确 SKU 读取和入队内容契合", async () => {
    renderDrawer({ item: { ...baseItem, product_sku: "AF-35MM-F18-PRO-FE" } });

    await waitFor(() => expect(getKolPoolContentFit).toHaveBeenCalledWith(
      "tok",
      42,
      { productSku: "AF-35MM-F18-PRO-FE" },
    ));

    analyzeKolPoolContentFit.mockClear();
    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(await screen.findByRole("button", { name: "开始深析" }));

    await waitFor(() => expect(analyzeKolPoolContentFit).toHaveBeenCalledWith(
      "tok",
      42,
      { force: false, productSku: "AF-35MM-F18-PRO-FE" },
    ));
  });

  it("共享或未关注 KOL 只读展示且不允许发起内容深析", async () => {
    getMyKolViewerContext.mockResolvedValueOnce({
      share_origin: { shared_by: 10, shared_by_name: "Owner" },
      paid_actions: { can_run_paid_actions: false, reason: "my_kol_paid_action_write_forbidden" },
    });
    renderDrawer();

    fireEvent.click(screen.getByText("深度分析"));
    const button = await screen.findByRole("button", { name: "关注后可深析" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(analyzeKolPoolContentFit).not.toHaveBeenCalled();
    expect(screen.getByText(/共享条目仅可查看已有结果/)).toBeInTheDocument();
  });

  it("单视频入队后轮询真实终态并自动回填详情", async () => {
    vi.useFakeTimers();
    const onReloadDetail = vi.fn().mockResolvedValue(undefined);
    getKolVideoAnalysisCache.mockResolvedValue({
      state: "ready",
      entry: {
        target_type: "video",
        target_id: "99",
        derive_method: "video_analysis_final_v1",
        status: "ready",
        result: {},
      },
    });
    const video = {
      evidence_id: 99,
      evidence_type: "video",
      content_url: "https://example.com/video/99",
      title: "ready after queue",
    };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: {
        status: "ready",
        item: { video_evidence: [video] },
        video_analysis: { items: [], summary: { evidence_count: 0, ready_count: 0, pending_count: 0 } },
      },
      onReloadDetail,
    });

    fireEvent.click(screen.getByRole("button", { name: "AI深度分析" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(enqueueVideoAnalysis).toHaveBeenCalledWith("tok", 42, 99);
    expect(onReloadDetail).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getKolVideoAnalysisCache).toHaveBeenCalledWith(
      "tok",
      99,
      "video_analysis_final_v1",
      { allowLocalEvaluationFallback: false },
    );
    expect(onReloadDetail).toHaveBeenCalledTimes(2);
    expect(screen.getByText("视频深析已完成并自动回填。")).toBeInTheDocument();
  });

  it("单视频 ai_disabled 显示精确门禁原因且不启动轮询", async () => {
    enqueueVideoAnalysis.mockResolvedValue({
      status: "ai_disabled",
      state: "not_requested",
      provider_gate_reason: "model_binding_blocked",
      model_readiness_status: "not_production_ready",
      provider_calls: false,
      write_db: false,
    });
    const video = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/video/99" };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: {
        status: "ready",
        item: { video_evidence: [video] },
        video_analysis: { items: [], summary: { evidence_count: 1, ready_count: 0, pending_count: 1 } },
      },
      onReloadDetail: vi.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole("button", { name: "AI深度分析" }));

    expect(await screen.findByText("精确视频模型尚未通过生产就绪，本次未入队（not_production_ready）。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "AI暂不可用" })).toBeEnabled();
    expect(getKolVideoAnalysisCache).not.toHaveBeenCalled();
  });

  it("单视频轮询遇到 not_requested 立即终止而非空转30分钟", async () => {
    vi.useFakeTimers();
    getKolVideoAnalysisCache.mockResolvedValue({
      target_id: "99",
      state: "not_requested",
      analysis_job: { state: "not_requested", reason: "model_binding_blocked" },
    });
    const video = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/video/99" };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: {
        status: "ready",
        item: { video_evidence: [video] },
        video_analysis: { items: [], summary: { evidence_count: 1, ready_count: 0, pending_count: 1 } },
      },
      onReloadDetail: vi.fn().mockResolvedValue(undefined),
    });

    fireEvent.click(screen.getByRole("button", { name: "AI深度分析" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("视频深析未完成：model_binding_blocked")).toBeInTheDocument();
    const callsAfterTerminal = getKolVideoAnalysisCache.mock.calls.length;
    await act(async () => {
      vi.advanceTimersByTime(20_000);
      await Promise.resolve();
    });
    expect(getKolVideoAnalysisCache).toHaveBeenCalledTimes(callsAfterTerminal);
  });

  it("切换 KOL 后丢弃上一位的延迟入队响应且不启动旧轮询", async () => {
    const pending = deferred<{ status: string }>();
    enqueueVideoAnalysis.mockImplementationOnce(() => pending.promise);
    const onReloadDetail = vi.fn().mockResolvedValue(undefined);
    const videoA = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/a.mp4" };
    const videoB = { evidence_id: 100, evidence_type: "video", content_url: "https://example.com/b.mp4" };
    const view = renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [videoA] },
      detailBundle: { status: "ready", item: { video_evidence: [videoA] }, video_analysis: { items: [], summary: { ready_count: 0 } } },
      onReloadDetail,
    });

    fireEvent.click(screen.getByRole("button", { name: "AI深度分析" }));
    view.rerender(
      <KOLDetailDrawer
        item={{ ...baseItem, id: 43, handle: "@next", posts_count: 1, avg_views: 100, video_evidence: [videoB] }}
        detailBundle={{ status: "ready", item: { video_evidence: [videoB] }, video_analysis: { items: [], summary: { ready_count: 0 } } }}
        apiToken="tok"
        onClose={() => {}}
        inMyList={false}
        onToggleMyList={() => {}}
        onContact={() => {}}
        onReloadDetail={onReloadDetail}
      />,
    );

    await act(async () => {
      pending.resolve({ status: "queued" });
      await pending.promise;
      await Promise.resolve();
    });

    expect(getKolVideoAnalysisCache).not.toHaveBeenCalled();
    expect(screen.queryByText(/已入队；左侧任务进度/)).toBeNull();
    expect(screen.getByRole("button", { name: "AI深度分析" })).toBeEnabled();
  });

  it("全视频 already_queued 也会逐 evidence 轮询到真实 ready 终态", async () => {
    vi.useFakeTimers();
    enqueueAllKolVideos.mockResolvedValue({
      status: "completed",
      evidence_total: 1,
      queued: 0,
      skipped: 1,
      items: [{ status: "already_queued", evidence_id: 99 }],
    });
    getKolVideoAnalysisBatch.mockResolvedValue({
      count: 1,
      items: [{ target_id: "99", state: "ready", entry: { status: "ready" } }],
    });
    const onReloadDetail = vi.fn().mockResolvedValue(undefined);
    const video = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/99.mp4" };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: { status: "ready", item: { video_evidence: [video] }, video_analysis: { items: [], summary: { ready_count: 0 } } },
      onReloadDetail,
    });
    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "KOL深度分析理解(最近20条)" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("button", { name: "深度分析入队中…" })).toBeDisabled();

    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getKolVideoAnalysisBatch).toHaveBeenCalledWith(
      "tok",
      [99],
      "video_analysis_final_v1",
    );
    expect(screen.getByText("全视频深析终态：1 条就绪。")).toBeInTheDocument();
  });

  it("全视频 batch 状态未变时不空转重拉 detail，终态变化才回填", async () => {
    vi.useFakeTimers();
    enqueueAllKolVideos.mockResolvedValue({
      status: "completed",
      evidence_total: 1,
      queued: 1,
      skipped: 0,
      items: [{ status: "queued", evidence_id: 99 }],
    });
    getKolVideoAnalysisBatch
      .mockResolvedValueOnce({
        count: 1,
        items: [{ target_id: "99", state: "running", analysis_job: { state: "running" } }],
      })
      .mockResolvedValueOnce({
        count: 1,
        items: [{ target_id: "99", state: "ready", entry: { status: "ready" } }],
      });
    const onReloadDetail = vi.fn().mockResolvedValue(undefined);
    const video = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/99.mp4" };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: { status: "ready", item: { video_evidence: [video] }, video_analysis: { items: [], summary: { ready_count: 0 } } },
      onReloadDetail,
    });
    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "KOL深度分析理解(最近20条)" }));
    await act(async () => { await Promise.resolve(); });

    // 启动时播种一次 detail；第一个 running batch 没有状态变化，不再重拉。
    expect(onReloadDetail).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onReloadDetail).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onReloadDetail).toHaveBeenCalledTimes(2);
    expect(screen.getByText("全视频深析终态：1 条就绪。")).toBeInTheDocument();
  });

  it("单视频与全视频使用独立轮询控制器，交叉启动不会互相取消", async () => {
    vi.useFakeTimers();
    enqueueVideoAnalysis.mockResolvedValue({ status: "queued" });
    enqueueAllKolVideos.mockResolvedValue({
      status: "completed",
      evidence_total: 1,
      queued: 0,
      skipped: 1,
      items: [{ status: "already_queued", evidence_id: 100 }],
    });
    getKolVideoAnalysisCache.mockImplementation(async (_token, evidenceId) => ({
      state: "ready",
      entry: { target_type: "video", target_id: String(evidenceId), derive_method: "video_analysis_final_v1", status: "ready" },
    }));
    getKolVideoAnalysisBatch.mockResolvedValue({
      count: 1,
      items: [{ target_id: "100", state: "ready", entry: { status: "ready" } }],
    });
    const onReloadDetail = vi.fn().mockResolvedValue(undefined);
    const videos = [
      { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/99.mp4" },
      { evidence_id: 100, evidence_type: "video", content_url: "https://example.com/100.mp4" },
    ];
    renderDrawer({
      item: { ...baseItem, posts_count: 2, avg_views: 100, video_evidence: videos },
      detailBundle: { status: "ready", item: { video_evidence: videos }, video_analysis: { items: [], summary: { ready_count: 0 } } },
      onReloadDetail,
    });

    fireEvent.click(screen.getByRole("button", { name: "AI深度分析" }));
    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "KOL深度分析理解(最近20条)" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => {
      vi.advanceTimersByTime(2_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getKolVideoAnalysisCache).toHaveBeenCalledWith(
      "tok",
      99,
      "video_analysis_final_v1",
      { allowLocalEvaluationFallback: false },
    );
    expect(getKolVideoAnalysisBatch).toHaveBeenCalledWith(
      "tok",
      [100],
      "video_analysis_final_v1",
    );
    expect(screen.getByText("视频深析已完成并自动回填。")).toBeInTheDocument();
    expect(screen.getByText("全视频深析终态：1 条就绪。")).toBeInTheDocument();
  });

  it("全视频批次的 ai_disabled 会立即形成诚实终态而非等待 30 分钟", async () => {
    enqueueAllKolVideos.mockResolvedValue({
      status: "completed",
      evidence_total: 1,
      queued: 0,
      skipped: 1,
      ai_disabled: 1,
      items: [{ status: "ai_disabled", evidence_id: 99, reason: "model_binding_blocked" }],
    });
    const video = { evidence_id: 99, evidence_type: "video", content_url: "https://example.com/99.mp4" };
    renderDrawer({
      item: { ...baseItem, posts_count: 1, avg_views: 100, video_evidence: [video] },
      detailBundle: { status: "ready", item: { video_evidence: [video] }, video_analysis: { items: [], summary: { ready_count: 0 } } },
      onReloadDetail: vi.fn().mockResolvedValue(undefined),
    });
    fireEvent.click(screen.getByText("深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "KOL深度分析理解(最近20条)" }));

    expect(await screen.findByText("全视频深析终态：0 条就绪，1 条未完成（model_binding_blocked）。")).toBeInTheDocument();
    expect(getKolVideoAnalysisBatch).not.toHaveBeenCalled();
    expect(getKolVideoAnalysisCache).toHaveBeenCalledTimes(2);
  });

  it("status=ready → 渲染长期记忆标题/内容风格/产品线/履约/独立徽标", async () => {
    getKolMemory.mockResolvedValue(readyMemory);
    renderDrawer();
    openCoopTab();

    expect(await screen.findByText("长期记忆")).toBeInTheDocument();
    expect(screen.getByText("硬核镜头测评 · 实拍对比")).toBeInTheDocument();
    expect(screen.getByText("镜头")).toBeInTheDocument();
    expect(screen.getByText("云台")).toBeInTheDocument();
    // 履约 4 格标签
    expect(screen.getByText("派单")).toBeInTheDocument();
    expect(screen.getByText("寄样")).toBeInTheDocument();
    expect(screen.getByText("失败任务")).toBeInTheDocument();
    // 独立于 V6 Fit 徽标
    expect(screen.getByText("独立于 V6 Fit · 不影响排序")).toBeInTheDocument();
    // 无 error boundary 文案
    expect(screen.queryByText(/出错|Something went wrong|Error/)).not.toBeInTheDocument();
  });

  it("红线:长期记忆 section 内绝不出现 v6_fit 数值(88)", async () => {
    getKolMemory.mockResolvedValue(readyMemory);
    renderDrawer();
    openCoopTab();

    const title = await screen.findByText("长期记忆");
    // 上溯到该区块容器(标题→徽标行→外层 px-5 py-4 区块)
    const section = title.closest("div.px-5")!;
    expect(section).toBeTruthy();
    // V6 Fit 数值 88 出现在抽屉头部,但绝不出现在长期记忆区
    expect(within(section as HTMLElement).queryByText("88")).toBeNull();
    expect((section as HTMLElement).textContent).not.toContain("viltrox");
  });

  it("status=missing → 渲染「暂无聚合数据」", async () => {
    getKolMemory.mockResolvedValue({ status: "missing" });
    renderDrawer();
    openCoopTab();
    expect(await screen.findByText("暂无聚合数据")).toBeInTheDocument();
  });

  it("独立联系人投影为 full 时展示完整邮箱，不依赖 detail item 明文", async () => {
    revealKolPoolContact.mockResolvedValueOnce({
      status: "full",
      kol_pool_id: 42,
      contact_masked: false,
      contacts: [
        { type: "email", value: "manager@example.com" },
        { type: "dm", channel: "instagram", value: "@futurestudio", source_label: "manual", verification_status: "verified" },
      ],
    });
    renderDrawer({
      item: {
        ...baseItem,
        email: "wrong-detail@example.com",
        contact_masked: false,
      },
    });

    expect(screen.getByRole("button", { name: "查看联系方式" })).toBeInTheDocument();
    expect(revealKolPoolContact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("manager@example.com")).toBeInTheDocument();
    expect(screen.getByText("Instagram")).toBeInTheDocument();
    expect(screen.getByText("@futurestudio")).toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();
    expect(screen.queryByText("wrong-detail@example.com")).toBeNull();
    expect(screen.queryByText("m***@e***")).toBeNull();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
    expect(revealKolPoolContact).toHaveBeenCalledWith("tok", 42, {
      signal: expect.any(AbortSignal),
      purpose: "kol_detail_view",
    });
  });

  it("masked seed 在单条详情读取期间不显示或复制星号地址", async () => {
    revealKolPoolContact.mockImplementationOnce(() => new Promise(() => undefined));
    renderDrawer({
      item: { ...baseItem, email: "m***@e***", contact_masked: true },
      detailLoading: true,
    });

    expect(screen.getByText("联系方式默认隐藏 · 点击后按当前权限审计读取")).toBeInTheDocument();
    expect(revealKolPoolContact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("正在读取完整联系方式…")).toBeInTheDocument();
    expect(screen.queryByText("m***@e***")).toBeNull();
    expect(screen.queryByRole("button", { name: /复制邮箱/ })).toBeNull();
  });

  it("detail 内容重渲染不会重复读取联系人，也不会采用 item 内的明文", async () => {
    revealKolPoolContact.mockResolvedValueOnce({
      status: "full",
      kol_pool_id: 42,
      contact_masked: false,
      contacts: [{ type: "email", value: "audited@example.com" }],
    });
    const view = renderDrawer({ item: { ...baseItem, email: "detail-one@example.com", contact_masked: false } });
    expect(revealKolPoolContact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("audited@example.com")).toBeInTheDocument();

    view.rerender(
      <KOLDetailDrawer
        item={{ ...baseItem, bio: "detail poll update", email: "detail-two@example.com", contact_masked: false }}
        apiToken="tok"
        onClose={() => {}}
        inMyList={false}
        onToggleMyList={() => {}}
        onContact={() => {}}
      />,
    );

    expect(screen.getByText("audited@example.com")).toBeInTheDocument();
    expect(screen.queryByText("detail-one@example.com")).toBeNull();
    expect(screen.queryByText("detail-two@example.com")).toBeNull();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
  });

  it("切换 KOL 或 token 会 abort 旧读取，迟到明文不能串到新抽屉", async () => {
    const first = deferred<any>();
    revealKolPoolContact
      .mockImplementationOnce(() => first.promise)
      .mockResolvedValueOnce({
        status: "full",
        kol_pool_id: 43,
        contact_masked: false,
        contacts: [{ type: "email", value: "current@example.com" }],
      });
    const view = renderDrawer({ item: { ...baseItem, id: 42 }, apiToken: "token-a" });
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    await waitFor(() => expect(revealKolPoolContact).toHaveBeenCalledTimes(1));
    const firstSignal = revealKolPoolContact.mock.calls[0][2].signal as AbortSignal;

    view.rerender(
      <KOLDetailDrawer
        item={{ ...baseItem, id: 43 }}
        apiToken="token-b"
        onClose={() => {}}
        inMyList={false}
        onToggleMyList={() => {}}
        onContact={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "查看联系方式" })).toBeInTheDocument();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("current@example.com")).toBeInTheDocument();
    expect(firstSignal.aborted).toBe(true);

    await act(async () => {
      first.resolve({
        status: "full",
        kol_pool_id: 42,
        contact_masked: false,
        contacts: [{ type: "email", value: "stale-secret@example.com" }],
      });
      await Promise.resolve();
    });
    expect(screen.queryByText("stale-secret@example.com")).toBeNull();
    expect(screen.getByText("current@example.com")).toBeInTheDocument();
  });

  it("masked + 空邮箱的 restricted 状态不显示未收集", async () => {
    revealKolPoolContact.mockResolvedValueOnce({
      status: "restricted",
      kol_pool_id: 42,
      contact_masked: true,
      contacts: [],
      reason: "audit_unavailable",
    });
    renderDrawer({ item: { ...baseItem, email: "", contact_masked: true } });

    expect(revealKolPoolContact).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("联系方式已受保护 · 当前账号不可读取明文")).toBeInTheDocument();
    expect(screen.queryByText(/未收集|暂无已验证/)).toBeNull();
  });

  it("关闭详情会清除并 abort 已读取的联系人明文", async () => {
    const onClose = vi.fn();
    revealKolPoolContact.mockResolvedValueOnce({
      status: "full",
      kol_pool_id: 42,
      contact_masked: false,
      contacts: [{ type: "email", value: "close-me@example.com" }],
    });
    renderDrawer({ onClose });
    fireEvent.click(screen.getByRole("button", { name: "查看联系方式" }));
    expect(await screen.findByText("close-me@example.com")).toBeInTheDocument();
    const signal = revealKolPoolContact.mock.calls[0][2].signal as AbortSignal;

    fireEvent.click(screen.getByRole("button", { name: "关闭详情" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("close-me@example.com")).toBeNull();
    expect(signal.aborted).toBe(true);
  });

  it("item=null → 组件返回 null(container 空)", () => {
    const { container } = render(
      <KOLDetailDrawer item={null} apiToken="tok" onClose={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("getKolMemory reject → 静默不抛,头部正常渲染", async () => {
    getKolMemory.mockRejectedValue(new Error("boom"));
    renderDrawer();
    // 头部 handle 仍渲染
    expect(await screen.findByText("@frank")).toBeInTheDocument();
    // 合作 tab 下也不渲染长期记忆区(kolMemory 为 null)
    openCoopTab();
    await waitFor(() => expect(getKolMemory).toHaveBeenCalled());
    expect(screen.queryByText("长期记忆")).not.toBeInTheDocument();
  });

  it("历史缓存把未知地区写成 X 时不显示假 CN", async () => {
    renderDrawer({ item: { ...baseItem, country: "", geo_tier: "X" } });

    expect(await screen.findByText("@frank")).toBeInTheDocument();
    expect(screen.queryByText("CN")).not.toBeInTheDocument();
  });

  it("新发现候选只显示真实入主表动作，不暴露待接入写入或更多占位", async () => {
    renderDrawer({ item: { ...baseItem, candidate_kind: "new_promoted" } });

    expect(await screen.findByText("新发现 · 高潜候选")).toBeInTheDocument();
    expect(screen.queryByText(/待接入写入/)).toBeNull();
    expect(screen.queryByText(/更多 · 待接入/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "入主表" }));
    await waitFor(() => expect(promoteKolPoolToMain).toHaveBeenCalledWith("tok", 42));
    expect(await screen.findByRole("button", { name: "已入主表 ✓" })).toBeInTheDocument();
  });
});

describe("KOLDetailDrawer 受众来源口径", () => {
  it("YouTube 329 评论者样本与本地评论桥 0 人分开展示", async () => {
    render(
      <KOLDrawerGeoDistribution
        item={{
        ...baseItem,
        platform: "youtube",
        audience_estimated: {
          sample_size: 329,
          confidence: "medium",
          generated_at: "2026-07-13T12:00:00Z",
          comments_scanned: 400,
          comment_intel: { sample_size: 400, source: "youtube_api_sample" },
          overlap: { items: [], self_commenters: 0 },
          source_contract: {
            profile_sample: {
              source: "youtube_data_api_live_sample",
              durable: false,
              commenters: 329,
              comments_scanned: 400,
            },
            overlap: { source: "vkpi_comments_pool_evidence", durable: true, commenters: 0 },
          },
        },
        }}
        geoDistribution={[]}
        apiToken=""
      />,
    );

    expect(await screen.findByText(/画像样本 329 评论者/)).toHaveTextContent("YouTube Data API 本次抽样");
    expect(screen.getByText(/画像样本 329 评论者/)).toHaveTextContent("本地评论桥 0 人");
    expect(screen.getByText(/未与本次 API 样本混算/)).toBeInTheDocument();
  });
});
