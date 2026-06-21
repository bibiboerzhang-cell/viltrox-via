import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// W5 recon: ActionInboxPanel 渲染 smoke。组件自取数据(listActionInbox),seam = actionInbox-api。
// 全 mock 掉 5 个 api,断言 items→标题/类别标签/操作按钮、空态、错误态、approve→execute 两步。hermetic(不打后端)。
const listActionInbox = vi.fn();
const approveAction = vi.fn();
const dismissAction = vi.fn();
const snoozeAction = vi.fn();
const executeAction = vi.fn();
vi.mock("../../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...a: unknown[]) => listActionInbox(...a),
  approveAction: (...a: unknown[]) => approveAction(...a),
  dismissAction: (...a: unknown[]) => dismissAction(...a),
  snoozeAction: (...a: unknown[]) => snoozeAction(...a),
  executeAction: (...a: unknown[]) => executeAction(...a),
}));

import { ActionInboxPanel } from "./ActionInboxPanel";

beforeEach(() => {
  listActionInbox.mockReset();
  approveAction.mockReset();
  dismissAction.mockReset();
  snoozeAction.mockReset();
  executeAction.mockReset();
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
    // approve 后本地转 approved → 露出「执行」按钮。
    const execBtn = await screen.findByRole("button", { name: /执行/ });
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
});
