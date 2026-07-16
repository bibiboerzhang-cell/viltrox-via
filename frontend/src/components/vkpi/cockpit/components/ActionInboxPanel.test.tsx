import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// W5 recon: ActionInboxPanel 渲染 smoke。组件自取数据(listActionInbox),seam = actionInbox-api。
// 全 mock 掉 5 个 api,断言 items→标题/类别标签/操作按钮、空态、错误态、approve→execute 两步。hermetic(不打后端)。
const listActionInbox = vi.fn();
const approveAction = vi.fn();
const dismissAction = vi.fn();
const snoozeAction = vi.fn();
const executeAction = vi.fn();
const reconcileAction = vi.fn();
const listRecentExecutionLedger = vi.fn(() => Promise.resolve({ items: [], available: true }));
vi.mock("../../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...a: unknown[]) => listActionInbox(...a),
  approveAction: (...a: unknown[]) => approveAction(...a),
  dismissAction: (...a: unknown[]) => dismissAction(...a),
  snoozeAction: (...a: unknown[]) => snoozeAction(...a),
  executeAction: (...a: unknown[]) => executeAction(...a),
  reconcileAction: (...a: unknown[]) => reconcileAction(...a),
  listRecentExecutionLedger: (...a: unknown[]) => listRecentExecutionLedger(...a),
}));

import { ActionInboxPanel } from "./ActionInboxPanel";

beforeEach(() => {
  listActionInbox.mockReset();
  approveAction.mockReset();
  dismissAction.mockReset();
  snoozeAction.mockReset();
  executeAction.mockReset();
  reconcileAction.mockReset();
});

// 可执行类(kol_profile 真实 requires_approval=true:writes_business_data 写 profile 字段)。
const baseItem = {
  id: 1,
  category: "kol_profile",
  title: "补全王红人资料",
  detail: "缺少邮箱与粉丝数",
  priority: "high",
  status: "suggested",
  requires_approval: true,
  uses_llm: false,
};

describe("ActionInboxPanel 渲染 smoke", () => {
  it("有 items:渲染标题 + 类别标签 + 三个操作按钮", async () => {
    listActionInbox.mockResolvedValue({ items: [baseItem], available: true, scope: "own" });
    render(<ActionInboxPanel apiToken="tok" limit={6} />);

    // 标题渲染
    expect(await screen.findByText("补全王红人资料")).toBeInTheDocument();
    // suggested 状态 → 三个操作按钮
    expect(screen.getByRole("button", { name: /通过/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /稍后/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /忽略/ })).toBeInTheDocument();
    // 面板头部
    expect(screen.getByText("今日建议")).toBeInTheDocument();
    // listActionInbox 被以 token + limit 调用
    expect(listActionInbox).toHaveBeenCalledWith("tok", { limit: 6 });
  });

  it("scope=own → footer 显示「仅我负责的」", async () => {
    listActionInbox.mockResolvedValue({ items: [baseItem], available: true, scope: "own" });
    render(<ActionInboxPanel apiToken="tok" />);
    await screen.findByText("补全王红人资料");
    expect(screen.getByText(/仅我负责的/)).toBeInTheDocument();
  });

  it("空态:items 为空 → 「暂无待办建议」", async () => {
    listActionInbox.mockResolvedValue({ items: [], available: true });
    render(<ActionInboxPanel apiToken="tok" />);
    expect(await screen.findByText(/暂无待办建议/)).toBeInTheDocument();
  });

  it("错误态:listActionInbox reject → 渲染「建议源异常」", async () => {
    listActionInbox.mockRejectedValue(new Error("boom"));
    render(<ActionInboxPanel apiToken="tok" />);
    expect(await screen.findByText(/建议源异常/)).toBeInTheDocument();
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("available=false → 「建议系统待启用」", async () => {
    listActionInbox.mockResolvedValue({ items: [], available: false });
    render(<ActionInboxPanel apiToken="tok" />);
    expect(await screen.findByText(/建议系统待启用/)).toBeInTheDocument();
  });

  it("无 apiToken → 不发请求,直接错误态「未登录 / 无 token」", async () => {
    render(<ActionInboxPanel apiToken="" />);
    await waitFor(() => expect(screen.getByText(/未登录 \/ 无 token/)).toBeInTheDocument());
    expect(listActionInbox).not.toHaveBeenCalled();
  });

  it("两步:通过 → 露出「执行」钮 → 执行成功后移除", async () => {
    // 第一次 load:suggested 有 baseItem,approved 空。
    listActionInbox.mockImplementation((_tok: unknown, params: any) =>
      Promise.resolve(
        params?.status === "approved"
          ? { items: [], available: true, scope: "own" }
          : { items: [baseItem], available: true, scope: "own" },
      ),
    );
    approveAction.mockResolvedValue({ ok: true, status: "approved", action_id: 1 });
    executeAction.mockResolvedValue({ ok: true, outcome: "success", category: "kol_profile" });

    render(<ActionInboxPanel apiToken="tok" />);
    // suggested → 先点「通过」。
    fireEvent.click(await screen.findByRole("button", { name: /通过/ }));
    await waitFor(() => expect(approveAction).toHaveBeenCalledWith("tok", 1));
    // approve 后本地转 approved → 露出「执行」按钮(锚定,避免匹配 footer「执行台账」)。
    const execBtn = await screen.findByRole("button", { name: /^执行$/ });
    // execute 二次确认:stub confirm 放行。
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(execBtn);
    await waitFor(() => expect(executeAction).toHaveBeenCalledWith("tok", 1));
    // 成功 → 该项移除。
    await waitFor(() => expect(screen.queryByText("补全王红人资料")).not.toBeInTheDocument());
    confirmSpy.mockRestore();
  });

  it("提醒类(requires_approval=false)→ 显示「知道了」而非「通过」(无执行器)", async () => {
    const reminder = {
      ...baseItem,
      id: 3,
      category: "event_followup",
      title: "活动收尾提醒",
      requires_approval: false,
    };
    listActionInbox.mockImplementation((_tok: unknown, params: any) =>
      Promise.resolve(
        params?.status === "approved"
          ? { items: [], available: true }
          : { items: [reminder], available: true, scope: "own" },
      ),
    );
    render(<ActionInboxPanel apiToken="tok" />);
    await screen.findByText("活动收尾提醒");
    expect(screen.getByRole("button", { name: /知道了/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /通过/ })).not.toBeInTheDocument();
  });

  it("executing 保持可见、禁止自动重试并可提交带证据人工对账", async () => {
    const executing = {
      ...baseItem,
      id: 8,
      title: "外部动作结果待核对",
      status: "executing",
      reconciliation_overdue: true,
      manual_reconciliation_required: true,
    };
    listActionInbox.mockImplementation((_tok: unknown, params: any) =>
      Promise.resolve(
        params?.status === "executing"
          ? { items: [executing], available: true, scope: "own" }
          : { items: [], available: true, scope: "own" },
      ),
    );
    reconcileAction.mockResolvedValue({
      ok: true,
      action_id: 8,
      decision: "succeeded",
      status: "executed",
      ledger_id: 91,
      correlation_id: "action-8-fixed",
      idempotent: false,
    });

    render(<ActionInboxPanel apiToken="tok" />);
    expect(await screen.findByText("外部动作结果待核对")).toBeInTheDocument();
    expect(screen.getByText("已超时 · 待人工核对")).toBeInTheDocument();
    expect(screen.getByText("禁止自动重试")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^执行$/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "人工对账" }));
    fireEvent.change(screen.getByLabelText("对账结论"), { target: { value: "succeeded" } });
    fireEvent.change(screen.getByLabelText("对账原因"), { target: { value: "已核对 provider 回执" } });
    fireEvent.change(screen.getByLabelText("对账证据"), {
      target: { value: "order:VKPI-42\nhttps://provider.example/receipt/42" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交对账" }));

    await waitFor(() => expect(reconcileAction).toHaveBeenCalledTimes(1));
    const [, actionId, payload] = reconcileAction.mock.calls[0];
    expect(actionId).toBe(8);
    expect(payload.decision).toBe("succeeded");
    expect(payload.reason).toBe("已核对 provider 回执");
    expect(payload.evidence).toEqual([
      { source: "manual", reference: "order:VKPI-42" },
      { source: "manual", reference: "https://provider.example/receipt/42" },
    ]);
    expect(payload.correlation_id).toMatch(/^action-8-/);
    await waitFor(() => expect(screen.queryByText("外部动作结果待核对")).not.toBeInTheDocument());
  });

  it("执行终态落账失败时转 executing 并保留在列表", async () => {
    const approved = { ...baseItem, id: 9, title: "可能已产生外部副作用", status: "approved" };
    listActionInbox.mockImplementation((_tok: unknown, params: any) =>
      Promise.resolve(
        params?.status === "approved"
          ? { items: [approved], available: true, scope: "own" }
          : { items: [], available: true, scope: "own" },
      ),
    );
    executeAction.mockResolvedValue({
      ok: false,
      outcome: "failed",
      reason: "execution_finalize_failed",
      detail: { manual_reconciliation_required: true },
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<ActionInboxPanel apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: /^执行$/ }));
    await waitFor(() => expect(executeAction).toHaveBeenCalledWith("tok", 9));
    expect(screen.getByText("可能已产生外部副作用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "人工对账" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^执行$/ })).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });
});
