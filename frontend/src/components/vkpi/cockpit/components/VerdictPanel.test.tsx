import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// 闭环波 L4:VerdictPanel 渲染逻辑测试(全 mock http seam,hermetic 不打后端)。
// 断言:context 读数渲染(预期/三窗/六键)、lesson 必填闸、decide POST 契约、
// 已裁决只读态、context 不可用的快照兜底、缺 outcome id 的诚实提示。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import { VerdictPanel, VERDICT_DECISIONS } from "./VerdictPanel";

const baseContext = {
  available: true,
  outcome: {
    id: 42,
    gtm_plan_id: "plan-af1",
    product_sku: "AF-85",
    market: "US",
    channel: "creator",
    action_type: "kol_outreach",
    expected_result: { reply_rate: ">=20%", posts: ">=3" },
    actual_result: {},
    window_7d: { contacted: 5, replied: 2 },
    window_14d: {},
    window_28d: {},
    decision: "open",
    lesson: null,
    decided_at: null,
  },
  weight_preview: {
    counts: { total: 1, actionable: 0, held: 1, recorded_only: 0 },
    min_sample: 5,
  },
};

beforeEach(() => {
  apiFetch.mockReset();
});

describe("VerdictPanel 裁决一屏", () => {
  it("context ready:渲染预期/三窗/权重预览 + 六个 decision 按钮(lesson 空时禁用)", async () => {
    apiFetch.mockResolvedValue(baseContext);
    render(<VerdictPanel apiToken="tok" verdictId={42} idType="outcome" />);

    // 头 + meta chips
    expect(await screen.findByText("裁决一屏 · 对答案")).toBeInTheDocument();
    expect(screen.getByText("SKU AF-85")).toBeInTheDocument();
    // 预期 kv 渲染
    expect(screen.getByText(/reply_rate/)).toBeInTheDocument();
    expect(screen.getByText(/>=20%/)).toBeInTheDocument();
    // 三窗:7d 有数,14d/28d 诚实未回填
    expect(screen.getByText(/contacted/)).toBeInTheDocument();
    expect(screen.getAllByText(/未回填/).length).toBeGreaterThanOrEqual(2);
    // 权重回流预览行(样本闸口径可见)
    expect(screen.getByText(/样本闸/)).toBeInTheDocument();
    expect(screen.getByText(/hold 1/)).toBeInTheDocument();
    // 六个 decision 按钮全在,且 lesson 未填时禁用
    for (const d of VERDICT_DECISIONS) {
      const btn = screen.getByRole("button", { name: d.label });
      expect(btn).toBeInTheDocument();
      expect(btn).toBeDisabled();
    }
    // context 以 GET 调对了 URL
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/admin/vkpi/gtm/verdicts/42/context?id_type=outcome",
      expect.objectContaining({ cache: "no-store" }),
      "tok",
    );
  });

  it("decide 流:填 lesson → 点「验证成立」→ POST decide 契约正确 → 已裁决态 + onDecided", async () => {
    apiFetch.mockImplementation((url: string) =>
      String(url).includes("/decide")
        ? Promise.resolve({ ok: true })
        : Promise.resolve(baseContext),
    );
    const onDecided = vi.fn();
    render(<VerdictPanel apiToken="tok" verdictId={42} idType="outcome" onDecided={onDecided} />);
    await screen.findByText("裁决一屏 · 对答案");

    fireEvent.change(screen.getByPlaceholderText(/lesson 一句话/), {
      target: { value: "回复率低于目标:下次先筛互动率" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证成立" }));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith(
        "/api/admin/vkpi/gtm/verdicts/42/decide",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ decision: "validated", lesson: "回复率低于目标:下次先筛互动率", id_type: "outcome" }),
        }),
        "tok",
      ),
    );
    // decided 即 finalized:成功后转只读回执
    expect(await screen.findByText(/已裁决:验证成立/)).toBeInTheDocument();
    expect(onDecided).toHaveBeenCalledWith("validated");
    // 按钮消失(不可重复裁决)
    expect(screen.queryByRole("button", { name: "证伪" })).not.toBeInTheDocument();
  });

  it("已裁决行:只读展示 decision + lesson,不给按钮", async () => {
    apiFetch.mockResolvedValue({
      ...baseContext,
      outcome: {
        ...baseContext.outcome,
        decision: "failed",
        lesson: "价格质疑集中,撤退该角度",
        decided_at: "2026-07-01T08:00:00Z",
      },
    });
    render(<VerdictPanel apiToken="tok" verdictId={42} idType="outcome" />);
    expect(await screen.findByText(/已裁决:证伪/)).toBeInTheDocument();
    expect(screen.getByText(/价格质疑集中/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "验证成立" })).not.toBeInTheDocument();
  });

  it("context 不可用(迁移 217 未上线)→ 快照兜底:payload.bet 预期 + why + 诚实标注,仍可裁决", async () => {
    apiFetch.mockResolvedValue({ available: false, reason: "GTM 结果账本尚未落地。" });
    render(
      <VerdictPanel
        apiToken="tok"
        verdictId={7}
        idType="inbox"
        fallback={{
          bet: { why: "US 市场 AF-85 创作者赛道预判上行", expected: { reply_rate: ">=20%" } },
        }}
      />,
    );
    expect(await screen.findByText(/账本读数不可用/)).toBeInTheDocument();
    expect(screen.getByText(/当时预判:/)).toBeInTheDocument();
    expect(screen.getByText(/US 市场 AF-85 创作者赛道预判上行/)).toBeInTheDocument();
    expect(screen.getByText(/reply_rate/)).toBeInTheDocument();
    // 兜底态仍给六键(裁决端点独立于读数)
    expect(screen.getByRole("button", { name: "撤退" })).toBeInTheDocument();
  });

  it("缺 outcome id → 诚实提示,不发请求不给按钮", async () => {
    render(<VerdictPanel apiToken="tok" verdictId={0} />);
    await waitFor(() =>
      expect(screen.getByText(/缺 gtm_outcome_id/)).toBeInTheDocument(),
    );
    expect(apiFetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "验证成立" })).not.toBeInTheDocument();
  });
});
