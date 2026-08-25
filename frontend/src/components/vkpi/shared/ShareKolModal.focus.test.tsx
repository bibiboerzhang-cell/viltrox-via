import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listKolShareMembers = vi.fn();

vi.mock("../../../services/vkpi/kol-api", () => ({
  listKolShareMembers: (...args: unknown[]) => listKolShareMembers(...args),
  shareKolToStaff: vi.fn(),
  unshareKolFromStaff: vi.fn(),
}));

import { ShareKolModal } from "./ShareKolModal";

beforeEach(() => {
  listKolShareMembers.mockReset().mockResolvedValue({ items: [] });
});

describe("ShareKolModal accessibility contract", () => {
  it("names the dialog, focuses close, handles Escape, and restores its opener", async () => {
    const opener = document.createElement("button");
    opener.textContent = "打开共享";
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();

    const view = render(
      <ShareKolModal
        kolPoolId="42"
        kolName="Frank Trades"
        staff={[]}
        apiToken="token"
        onClose={onClose}
      />,
    );

    expect(screen.getByRole("dialog", { name: "共享 KOL 给成员" })).toHaveAttribute("aria-modal", "true");
    const close = screen.getByRole("button", { name: "关闭共享 KOL" });
    await waitFor(() => expect(close).toHaveFocus());
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    view.unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});
