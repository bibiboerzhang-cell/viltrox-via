import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModalShell } from "./MarketVoicePage.dialogs";

afterEach(() => {
  document.body.style.overflow = "";
});

describe("ModalShell body portal and keyboard contract", () => {
  it("escapes transformed grid ancestors and keeps exactly one scrolling content region", async () => {
    const host = document.createElement("div");
    host.style.transform = "translate3d(0, 0, 0)";
    host.style.overflow = "hidden";
    document.body.appendChild(host);

    const view = render(
      <ModalShell title="KOL 详情" sub="完整内容" onClose={vi.fn()}>
        <div style={{ height: 1600 }}>底部动作</div>
      </ModalShell>,
      { container: host },
    );

    const layer = document.querySelector<HTMLElement>('[data-vkpi-modal-layer="body-portal"]');
    expect(layer?.parentElement).toBe(document.body);
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-labelledby");
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-describedby");
    expect(document.querySelectorAll('[data-vkpi-modal-scroll="content"]')).toHaveLength(1);
    expect(document.querySelector('[data-vkpi-modal-scroll="content"]')).toHaveClass("overflow-y-auto");

    await waitFor(() => expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus());
    view.unmount();
    expect(document.body.style.overflow).toBe("");
    host.remove();
  });

  it("traps tab focus, closes only the top layer with Escape, and restores prior focus", async () => {
    const closeLower = vi.fn();
    const closeUpper = vi.fn();
    const opener = document.createElement("button");
    opener.textContent = "打开 KOL";
    document.body.appendChild(opener);
    opener.focus();

    const view = render(
      <>
        <ModalShell title="底层" onClose={closeLower}>
          <button type="button">底层动作</button>
        </ModalShell>
        <ModalShell title="顶层" onClose={closeUpper}>
          <input aria-label="顶层输入" />
          <button type="button">顶层末尾动作</button>
        </ModalShell>
      </>,
    );

    const closeButtons = screen.getAllByRole("button", { name: "关闭" });
    await waitFor(() => expect(closeButtons[1]).toHaveFocus());
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "顶层末尾动作" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(closeButtons[1]).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(closeUpper).toHaveBeenCalledTimes(1);
    expect(closeLower).not.toHaveBeenCalled();

    view.unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});
