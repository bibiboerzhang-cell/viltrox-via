import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

// 自治驾照板块页范式改版冒烟(金样板 MarketVoicePage/GtmCommandBoardPage.smoke 同构):
// - 页壳:pagehead(自治驾照 L0-L4 + 刷新 + 编辑布局)+ 可编辑看板;
// - KPI 带四卡:现值全真(驾照=licenses / 已对答案+待对答案=prediction-ledger totals /
//   待人审建议=actions/inbox suggested 近 200 窗口),该域无时序端点 → 四卡 spempty
//   诚实虚线零环比药丸;
// - 默认四行:lic(评估两键+驾照卡族+人工调级)+ gates(闸门登记表,默认 OFF 如实)
//   + approvals/ledger/scorecard/loop 四件 embeds(旧组件零改动收编);
// - 旧页功能零丢失:五维 chips /「影响评分 永久禁止」/ 台账四读数 / 最近升降 /
//   评估演练→写库(confirm 闸)/ 人工调级双必填校验;
// - palette 备选两件(miss/shadow)不进默认布局(未挂载即零取数);
// - 布局键 vkpi-autonomy-layout-v1 + 不传 apiToken → 绝不写账户级 dashboard 布局。
// mock seam:services/http.apiFetch(全页唯一网络出口,embeds 旧组件同缝)。

const apiFetchMock = vi.fn();
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { AutonomyDrivePage } from "./AutonomyDrivePage";

const LIC_ITEM = (action_type: string, extra: Record<string, unknown> = {}) => ({
  action_type,
  level: 1,
  level_label: "建议",
  dimensions: { write_db: true, call_llm: false, spend_money: false, contact_external: false, change_project_status: false, affect_scoring: false },
  hit_rate_snapshot: null,
  sample_count: 0,
  last_change_reason: "initial_seed",
  changed_at: "2026-07-07T07:08:54Z",
  ledger: { status: "pending", hit_rate: null, sample_count: 0, reason: "待对答案:有预测无结果,暂无法计算命中率" },
  ...extra,
});

const LIC_OK = {
  status: "ready",
  items: [
    LIC_ITEM("comment_reply_draft"),
    LIC_ITEM("kol_recommend", { ledger: { status: "ok", hit_rate: 0.6, sample_count: 10 } }),
    LIC_ITEM("outreach_draft"),
    LIC_ITEM("pool_enrich"),
    LIC_ITEM("report_generate"),
  ],
  rules: {
    promote: "近20次命中率 >= 0.85 且样本 >= 20 → 升1级(自动封顶 L3,L4 仅人工)",
    demote: "连续 5 次未命中 → 降1级(保底 L0)",
    hold: "台账缺席 / 样本不足 / 命中率不够 → 原地不动并说明",
    forbidden_dimension: "affect_scoring(影响评分)永久 False,不可经任何路径修改",
  },
  note: "驾照只回答「许不许」;本轮只建判定与展示,不真执行任何外部动作。影响评分维度永久禁止。",
  generated_at: "2026-07-12T08:00:00Z",
};

const LEDGER_OK = {
  status: "ok",
  generated_at: "2026-07-12T08:00:00Z",
  window: 20,
  groups: [
    { action_type: "kol_recommend", label: "KOL 推荐", status: "ok", hit_rate: 0.6, sample_count: 10, confidence: "low", basis: { hits: 6, misses: 4, window: 20, pending_count: 3 } },
    { action_type: "outreach_draft", label: "外联草稿", status: "pending", hit_rate: null, sample_count: 0, confidence: "none", basis: { pending_count: 12, pending_definition: "待对答案:有预测无结果" } },
  ],
  totals: { groups: 5, judged_total: 22, pending_total: 15, groups_with_sample: 2 },
};

// 近 200 条 suggested 窗口:count=196(K4 同源);条目给 2 条非 gtm_verdict 类
const INBOX_OK = {
  items: [
    { id: 11, category: "failed_retry", title: "重试:失败任务 #88", detail: "上次失败原因:超时", status: "suggested", priority: "high", requires_approval: true },
    { id: 12, category: "kol_profile", title: "补全:@alpha 主页资料", detail: "缺粉丝数", status: "suggested", priority: "medium", requires_approval: true },
  ],
  available: true,
  count: 196,
  scope: "all",
  today_summary: { today_executed_count: 2, today_approved_count: 1 },
};

const SCORECARD_OK = {
  status: "ok",
  weeks: 8,
  generated_at: "2026-07-12T08:00:00Z",
  min_sample_per_week: 5,
  week_axis: [{ week: "2026-W24" }, { week: "2026-W28" }],
  groups: [
    {
      action_type: "kol_recommend",
      label: "KOL 推荐",
      status: "ready",
      weekly: [{ week: "2026-W28", judged: 3, hits: 2, hit_rate: 0.67, sparse: true }],
      in_range_judged: 3,
      in_range_hits: 2,
      in_range_hit_rate: 0.67,
      sparse_weeks: 1,
      pending_count: 4,
      momentum: { direction: "flat", delta_pp: 0 },
    },
  ],
  overall: { weekly: [] },
  momentum: { direction: "stalled" },
  pending_backlog: {
    pending_total: 15,
    judged_total_all_time: 22,
    pending_share: 0.4,
    headline: "15 条预测还没对答案",
    by_group: [],
    chase_top5: [{ action_type: "kol_recommend", label: "KOL 推荐", ref: "reco#9", detail: "候选推荐待复盘", pending_since: "2026-06-20", age_days: 22 }],
  },
};

const TRACE_EMPTY = { status: "empty", reason: "尚无串跑记录", items: [] };

const EVAL_DRY = {
  status: "ready",
  dry_run: true,
  window: 20,
  items: [
    { action_type: "kol_recommend", current_level: 1, decision: "hold", proposed_level: 1, reason: "样本 10 < 20,样本不足不动级", applied: false },
  ],
  evaluated_at: "2026-07-12T08:05:00Z",
};

const EVAL_WRITE = { ...EVAL_DRY, dry_run: false, items: [{ ...EVAL_DRY.items[0], decision: "demote", proposed_level: 0, applied: true }] };

const OVERRIDE_OK = { status: "ok", action_type: "kol_recommend", level: 2, previous_level: 1 };

function routeApi(overrides: { licenses?: unknown } = {}) {
  apiFetchMock.mockReset().mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/api/admin/vkpi/autonomy/licenses/") && p.includes("/override")) return OVERRIDE_OK;
    if (p.startsWith("/api/admin/vkpi/autonomy/licenses")) {
      const value = overrides.licenses ?? LIC_OK;
      if (value instanceof Error) throw value;
      return value;
    }
    if (p.startsWith("/api/admin/vkpi/autonomy/evaluate")) return p.includes("dry_run=false") ? EVAL_WRITE : EVAL_DRY;
    if (p.startsWith("/api/admin/vkpi/prediction-ledger/summary")) return LEDGER_OK;
    if (p.startsWith("/api/admin/vkpi/actions/inbox")) return INBOX_OK;
    if (p.startsWith("/api/admin/vkpi/learning/weekly-scorecard")) return SCORECARD_OK;
    if (p.startsWith("/api/admin/vkpi/agents/loop/trace")) return TRACE_EMPTY;
    throw new Error(`unexpected apiFetch: ${p}`);
  });
}

let tokenSeq = 0;
const renderBoard = (props: Record<string, unknown> = {}) =>
  render(<AutonomyDrivePage apiToken={`t${++tokenSeq}`} {...(props as any)} />);

const calledPaths = () => apiFetchMock.mock.calls.map((call) => String(call[0]));

beforeEach(() => {
  window.localStorage.clear();
  routeApi();
});

describe("AutonomyDrivePage smoke(页壳 + KPI 带真数 + 默认四行 + 驾照动作 + 布局键)", () => {
  it("KPI 带四卡现值全真;自治域无时序端点 → 四卡诚实虚线零药丸;页壳按钮动词直说", async () => {
    expect(() => renderBoard()).not.toThrow();
    expect(await screen.findByText("自治总览")).toBeTruthy();

    await waitFor(() => {
      const vals = [...document.querySelectorAll(".ds-kpi__val")].map((el) => (el.textContent || "").trim());
      expect(vals).toContain("5张"); // 驾照 = licenses items 5 张
      expect(vals).toContain("22条"); // 已对答案 = totals.judged_total
      expect(vals).toContain("15条"); // 待对答案 = totals.pending_total
      expect(vals).toContain("196条"); // 待人审建议 = inbox suggested 近 200 窗口 count
    });
    expect(document.querySelectorAll(".ds-kpi").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__series-empty").length).toBe(4);
    expect(document.querySelectorAll(".ds-kpi__delta").length).toBe(0);

    expect(screen.getByText("自治驾照 L0-L4")).toBeTruthy();
    expect(screen.getByText("编辑布局")).toBeTruthy();
    expect(screen.getByText("刷新")).toBeTruthy();
  });

  it("旧页功能零丢失:驾照卡族(五维 / 永久禁止 / 四读数 / 最近升降 / 调级控件)+ gates 登记表 + 四件 embeds", async () => {
    renderBoard();
    expect(await screen.findByText("驾照与调级")).toBeTruthy();

    // 五张驾照卡真身(KOL 推荐 / 外联草稿在台账 embed 组行同名双出现,取 All)
    expect((await screen.findAllByText("KOL 推荐")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("外联草稿").length).toBeGreaterThan(0);
    expect(screen.getByText("评论回复草稿")).toBeTruthy();
    expect(screen.getByText("Pool 补全")).toBeTruthy();
    expect(screen.getByText("报告生成")).toBeTruthy();

    // 五维 chips(写库 许 ×5)+ 影响评分永久禁止(红线语义原样,每卡一枚)
    expect(screen.getAllByText("写库 许").length).toBe(5);
    expect(screen.getAllByText("调用AI 禁").length).toBe(5);
    expect(screen.getAllByText("影响评分 永久禁止").length).toBe(5);

    // 台账四读数 + 诚实 pending 原因 + 最近升降(绝对时间戳,非相对)
    expect(screen.getAllByText("台账命中率(近20次)").length).toBe(5);
    expect(screen.getAllByText(/待对答案:有预测无结果/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("最近一次升降(表只存最近一条,非全量历史)").length).toBe(5);
    expect(screen.getAllByText("initial_seed").length).toBeGreaterThan(0);

    // 人工调级控件:L4 仅人工可授选项 + reason 必填占位
    expect(screen.getAllByText("L4 全自主(仅人工可授)").length).toBe(5);
    expect(screen.getAllByPlaceholderText("调级理由(必填)").length).toBe(5);

    // gates 登记表:两条永久禁止 + 默认关 / 恒演练 如实标
    expect(screen.getByText("权限闸门")).toBeTruthy();
    expect(screen.getAllByText("永久禁止").length).toBe(2);
    expect(screen.getByText("自我提权")).toBeTruthy();
    expect(screen.getByText("默认关")).toBeTruthy();
    expect(screen.getByText("恒演练")).toBeTruthy();
    expect(screen.getByText("默认演练")).toBeTruthy();
    expect(screen.getByText("仅人工")).toBeTruthy();

    // 审批流 embed:今日建议条目真身(状态机按钮由旧组件渲染)
    expect(await screen.findByText("重试:失败任务 #88")).toBeTruthy();
    expect(screen.getByText("补全:@alpha 主页资料")).toBeTruthy();

    // 台账 embed:组行 + 样本徽;记分卡 embed:积压红条 + 催办名单;闭环 embed:演练钮 + 诚实空态
    expect(screen.getByText("2/2 组有样本")).toBeTruthy();
    expect(screen.getByText("条预测待对答案")).toBeTruthy();
    expect(screen.getByText("最老欠账 TOP5 · 谁该被催对答案")).toBeTruthy();
    expect(screen.getByText("串跑一遍(dry-run)")).toBeTruthy();
    expect(screen.getByText("尚无串跑记录")).toBeTruthy();

    // palette 备选不进默认布局(未挂载即零取数)
    expect(screen.queryByText("预览入记忆")).toBeNull();
    expect(screen.queryByText(/赢旧版才上线/)).toBeNull();
    const paths = calledPaths();
    expect(paths.some((pp) => pp.includes("miss-review"))).toBe(false);
    expect(paths.some((pp) => pp.includes("shadow-evals"))).toBe(false);
  });

  it("评估升降:演练零确认直发 dry_run=true;写库先 confirm 再发 dry_run=false 并重拉驾照", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderBoard();
    fireEvent.click(await screen.findByText("评估升降(演练)"));
    expect(await screen.findByText("评估结果(演练,未落库)")).toBeTruthy();
    // 「不动」在 SrcChip 口径行(rules.hold 键)同名出现,取 All
    expect(screen.getAllByText("不动").length).toBeGreaterThan(0);
    expect(screen.getByText(/样本 10 < 20/)).toBeTruthy();
    expect(confirmSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("执行升降(写库)"));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("评估结果(已执行)")).toBeTruthy();
    expect(screen.getByText("降1级 L1→L0")).toBeTruthy();
    expect(screen.getByText("已落库")).toBeTruthy();

    const evalCalls = calledPaths().filter((pp) => pp.includes("/autonomy/evaluate"));
    expect(evalCalls).toEqual([
      "/api/admin/vkpi/autonomy/evaluate?dry_run=true",
      "/api/admin/vkpi/autonomy/evaluate?dry_run=false",
    ]);
    // 写库后重拉驾照(licenses 至少两次)
    await waitFor(() => {
      const licCalls = calledPaths().filter((pp) => pp === "/api/admin/vkpi/autonomy/licenses");
      expect(licCalls.length).toBeGreaterThan(1);
    });
    confirmSpy.mockRestore();
  });

  it("confirm 取消 → 写库零请求(危险操作可反悔)", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderBoard();
    fireEvent.click(await screen.findByText("执行升降(写库)"));
    expect(calledPaths().filter((pp) => pp.includes("dry_run=false")).length).toBe(0);
    confirmSpy.mockRestore();
  });

  it("人工调级:级别 + reason 双必填校验;端点真实返回才落回执", async () => {
    renderBoard();
    await screen.findAllByText("KOL 推荐");
    const buttons = screen.getAllByText("确认调级");
    // 未选级别
    fireEvent.click(buttons[1]); // 卡序按 action_type 字母序:kol_recommend 第 2 张
    expect(await screen.findByText("先选目标级别")).toBeTruthy();
    expect(calledPaths().some((pp) => pp.includes("/override"))).toBe(false);

    // 选级别但没写 reason
    const selects = document.querySelectorAll("select");
    fireEvent.change(selects[1], { target: { value: "2" } });
    fireEvent.click(buttons[1]);
    expect(await screen.findByText("人工调级必须写 reason")).toBeTruthy();
    expect(calledPaths().some((pp) => pp.includes("/override"))).toBe(false);

    // 双必填齐 → 真发 override,回执用端点真实返回(L2 原 L1)
    const inputs = screen.getAllByPlaceholderText("调级理由(必填)");
    fireEvent.change(inputs[1], { target: { value: "冒烟调级验证" } });
    fireEvent.click(buttons[1]);
    expect(await screen.findByText("已调至 L2(原 L1)")).toBeTruthy();
    const overrideCalls = calledPaths().filter((pp) => pp.includes("/override"));
    expect(overrideCalls).toEqual(["/api/admin/vkpi/autonomy/licenses/kol_recommend/override"]);
  });

  it("诚实空态:驾照表不可读 → 空态短句带后端 reason,绝不编卡", async () => {
    routeApi({ licenses: { status: "empty", reason: "驾照表不可读(迁移212未apply或异常)", items: [], rules: {} } });
    renderBoard();
    // KPI 驾照卡 pending 注 + lic 模块空态短句双处如实透出后端 reason
    expect((await screen.findAllByText(/驾照表不可读/)).length).toBeGreaterThan(0);
    // 驾照卡族不渲染(台账 embed 的组行「KOL 推荐」不受驾照空态影响,仍如实在)
    expect(screen.queryByText("评论回复草稿")).toBeNull();
    expect(screen.queryByText("确认调级")).toBeNull();
  });

  it("palette 全量可选:编辑布局 → 添加模块 弹层列出备选两件", async () => {
    renderBoard();
    expect(await screen.findByText("驾照与调级")).toBeTruthy();
    fireEvent.click(screen.getByText("编辑布局"));
    fireEvent.click(screen.getByText("添加模块"));
    await waitFor(() => expect(screen.getByText("低命中复盘")).toBeTruthy());
    expect(screen.getByText("影子评测")).toBeTruthy();
  });

  it("布局键 vkpi-autonomy-layout-v1 生效;不传 apiToken 给板组件 → 绝不写账户级 dashboard 布局", async () => {
    window.localStorage.setItem("vkpi-autonomy-layout-v1", JSON.stringify([{ moduleKey: "kpiA", span: 12 }]));
    renderBoard();
    expect(await screen.findByText("自治总览")).toBeTruthy();
    expect(screen.queryByText("驾照与调级")).toBeNull();
    expect(screen.queryByText("审批流 · 今日建议")).toBeNull();
    expect(calledPaths().some((pp) => pp.includes("preference"))).toBe(false);
  });
});
