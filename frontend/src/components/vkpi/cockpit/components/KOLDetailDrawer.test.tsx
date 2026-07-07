import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, waitFor, fireEvent } from "@testing-library/react";

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
const enqueueVideoAnalysis = vi.fn();
const getKolPoolAccountDossier = vi.fn();
const enqueueAllKolVideos = vi.fn();
const getKolCooperation = vi.fn();
const recordKolCooperation = vi.fn();
const promoteKolPoolToMain = vi.fn();
// A1 自动补档:baseItem 档案瘦(无帖数/均播)→ 抽屉挂载即入队 profile 深爬,mock 必须供齐,
// 否则 effect 内调用 undefined 直接炸整组用例(此前缺此 mock 是本文件 4/5 红的真因)。
const enqueueKolProfileCrawl = vi.fn();
const refreshAudienceStats = vi.fn();
const getKolVideoAnalysisCache = vi.fn();
vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  getKolPoolDimensions11: (...a: unknown[]) => getKolPoolDimensions11(...a),
  getKolPoolLlmDeepAnalysis: (...a: unknown[]) => getKolPoolLlmDeepAnalysis(...a),
  getKolPoolContentFit: (...a: unknown[]) => getKolPoolContentFit(...a),
  enqueueVideoAnalysis: (...a: unknown[]) => enqueueVideoAnalysis(...a),
  getKolPoolAccountDossier: (...a: unknown[]) => getKolPoolAccountDossier(...a),
  enqueueAllKolVideos: (...a: unknown[]) => enqueueAllKolVideos(...a),
  getKolCooperation: (...a: unknown[]) => getKolCooperation(...a),
  recordKolCooperation: (...a: unknown[]) => recordKolCooperation(...a),
  promoteKolPoolToMain: (...a: unknown[]) => promoteKolPoolToMain(...a),
  enqueueKolProfileCrawl: (...a: unknown[]) => enqueueKolProfileCrawl(...a),
  refreshAudienceStats: (...a: unknown[]) => refreshAudienceStats(...a),
  getKolVideoAnalysisCache: (...a: unknown[]) => getKolVideoAnalysisCache(...a),
}));

const getKolMemory = vi.fn();
vi.mock("../../../../services/vkpi/kolMemory-api", () => ({
  getKolMemory: (...a: unknown[]) => getKolMemory(...a),
}));

import { KOLDetailDrawer } from "./KOLDetailDrawer";

beforeEach(() => {
  getKolPoolDimensions11.mockReset().mockResolvedValue({ status: "missing" });
  getKolPoolLlmDeepAnalysis.mockReset().mockResolvedValue({ status: "missing" });
  getKolPoolContentFit.mockReset().mockResolvedValue({ status: "missing" });
  enqueueVideoAnalysis.mockReset().mockResolvedValue({ status: "queued" });
  getKolPoolAccountDossier.mockReset().mockResolvedValue({ status: "missing" });
  enqueueAllKolVideos.mockReset().mockResolvedValue({ status: "queued" });
  getKolCooperation.mockReset().mockResolvedValue(null);
  recordKolCooperation.mockReset().mockResolvedValue({ status: "" });
  promoteKolPoolToMain.mockReset().mockResolvedValue({ ok: true });
  enqueueKolProfileCrawl.mockReset().mockResolvedValue({ status: "queued" });
  refreshAudienceStats.mockReset().mockResolvedValue({ status: "ok" });
  getKolVideoAnalysisCache.mockReset().mockResolvedValue({ state: "missing" });
  getKolMemory.mockReset().mockResolvedValue(null);
  // C1 tab 记忆落 localStorage:清掉,让每条用例都从默认「概览」出发(需要合作 tab 的用例自行点击)。
  window.localStorage.clear();
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
});
