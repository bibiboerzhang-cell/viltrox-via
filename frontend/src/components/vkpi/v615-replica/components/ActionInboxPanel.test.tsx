import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// W5 recon: ActionInboxPanel 渲染 smoke。组件自取数据(listActionInbox),seam = actionInbox-api。
// 全 mock 掉 4 个 api,断言 items→标题/类别标签/操作按钮、空态、错误态。hermetic(不打后端)。
const listActionInbox = vi.fn();
const approveAction = vi.fn();
const dismissAction = vi.fn();
const snoozeAction = vi.fn();
vi.mock("../../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...a: unknown[]) => listActionInbox(...a),
  approveAction: (...a: unknown[]) => approveAction(...a),
  dismissAction: (...a: unknown[]) => dismissAction(...a),
  snoozeAction: (...a: unknown[]) => snoozeAction(...a),
}));

import { ActionInboxPanel } from "./ActionInboxPanel";

beforeEach(() => {
  listActionInbox.mockReset();
  approveAction.mockReset();
  dismissAction.mockReset();
  snoozeAction.mockReset();
});

const baseItem = {
  id: 1,
  category: "kol_profile",
  title: "补全王红人资料",
  detail: "缺少邮箱与粉丝数",
  priority: "high",
  status: "suggested",
  requires_approval: false,
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
});
