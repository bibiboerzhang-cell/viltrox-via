import { describe, it, expect, vi } from "vitest";
import React from "react";
import { render, screen } from "@testing-library/react";

// PopoverWrapper 有定位/portal,桩成直接渲染 children。
vi.mock("./PopoverWrapper", () => ({
  PopoverWrapper: ({ children }: any) => React.createElement("div", null, children),
}));
// ActionInboxPanel 自身会 fetch action-inbox;此处只验「工作提醒里有它的槽位」,桩掉内部。
vi.mock("../ActionInboxPanel", () => ({
  ActionInboxPanel: () => React.createElement("div", { "data-testid": "action-inbox-stub" }, "今日建议内容"),
}));

import { WorkRemindersPopover } from "./WorkRemindersPopover";

describe("WorkRemindersPopover 含今日建议", () => {
  it("挂载不抛异常,工作提醒标题 + 今日建议(Action Inbox)槽位都在", () => {
    expect(() =>
      render(
        React.createElement(WorkRemindersPopover, {
          onClose: () => {},
          reminders: [],
          t: (x: string) => x,
          anchorRef: { current: null },
          apiToken: "t",
        }),
      ),
    ).not.toThrow();

    expect(screen.getByText("工作提醒")).toBeTruthy();
    expect(screen.getByText("今日建议")).toBeTruthy();
    expect(screen.getByTestId("action-inbox-stub")).toBeTruthy();
  });

  it("无 apiToken 时不渲染今日建议槽位(降级安全)", () => {
    render(
      React.createElement(WorkRemindersPopover, {
        onClose: () => {},
        reminders: [],
        t: (x: string) => x,
        anchorRef: { current: null },
      }),
    );
    expect(screen.queryByTestId("action-inbox-stub")).toBeNull();
  });
});
