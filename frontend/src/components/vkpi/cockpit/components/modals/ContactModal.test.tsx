import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const revealKolPoolContact = vi.fn();
const apiFetch = vi.fn();

vi.mock("../../../../../services/vkpi/kolPool-api", () => ({
  revealKolPoolContact: (...args: unknown[]) => revealKolPoolContact(...args),
}));

vi.mock("../../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { ContactModal } from "./ContactModal";

const item = {
  id: 42,
  display_name: "Future Shock Studios",
  handle: "UChYZp0fnZylVSXmAoocVS_w",
  channel_id: "UChYZp0fnZylVSXmAoocVS_w",
  platform: "youtube",
  profile_url: "https://www.youtube.com/channel/UChYZp0fnZylVSXmAoocVS_w",
  email: "m***@g***",
  contact_masked: true,
};

beforeEach(() => {
  revealKolPoolContact.mockReset();
  apiFetch.mockReset().mockResolvedValue({});
});

describe("ContactModal audited contact reveal", () => {
  it("uses a full list/detail projection immediately without another reveal request", () => {
    render(
      <ContactModal
        item={{
          ...item,
          email: "manager@example.com",
          contact_masked: false,
          other_contacts_json: [{ contact_type: "whatsapp", contact_value: "+12025550199" }],
          contact_channels: { email: { type: "email", masked_value: "m***@e***" } },
        }}
        apiToken="token"
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("manager@example.com")).toBeTruthy();
    expect(screen.getByText("WhatsApp")).toBeTruthy();
    expect(screen.getByText("+12025550199")).toBeTruthy();
    expect(screen.queryByText("m***@e***")).toBeNull();
    expect(screen.queryByText("正在授权读取联系方式…")).toBeNull();
    expect(revealKolPoolContact).not.toHaveBeenCalled();
  });

  it("reveals one email only inside the open modal and uses the display name", async () => {
    revealKolPoolContact.mockResolvedValue({
      status: "revealed",
      kol_pool_id: 42,
      email: "manager@example.com",
      other_contacts: [],
      contact_masked: false,
    });

    render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);

    expect(screen.getByText("正在授权读取联系方式…")).toBeTruthy();
    expect(await screen.findByText("manager@example.com")).toBeTruthy();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
    expect(revealKolPoolContact).toHaveBeenCalledWith(
      "token",
      42,
      { signal: expect.any(AbortSignal) },
    );
    expect(screen.getByText("Future Shock Studios")).toBeTruthy();
    expect(screen.queryByText(item.handle)).toBeNull();
    expect(document.body.textContent).not.toContain(item.handle);
    expect(screen.getByDisplayValue(/Viltrox × Future Shock Studios/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "添加额外渠道" }));
    expect(screen.getByRole("link", { name: "打开 YouTube 主页" })).toHaveAttribute("href", item.profile_url);
    expect(document.body.textContent).not.toContain(item.handle);
    fireEvent.click(screen.getByRole("button", { name: "邮件邀请" }));
  });

  it("reveals a legacy masked non-email channel without inventing an email", async () => {
    revealKolPoolContact.mockResolvedValue({
      status: "revealed",
      kol_pool_id: 44,
      email: "",
      other_contacts: [{ contact_type: "link", platform: "ig_dm", contact_value: "@creator" }],
      contact_masked: false,
    });
    render(
      <ContactModal
        item={{ ...item, id: 44, email: "", contact_masked: true }}
        apiToken="token"
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByText("Instagram")).toBeTruthy();
    expect(screen.getByText("@creator")).toBeTruthy();
    expect(screen.queryByText("mailto:@creator")).toBeNull();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
  });

  it("shows a permission error for a rejected reveal without exposing guessed data", async () => {
    revealKolPoolContact.mockRejectedValue({ status: 403 });

    render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("当前账号没有查看完整联系方式的权限");
    expect(screen.getByText("m***@g***")).toBeTruthy();
  });

  it("explains rate limiting and retries through a fresh audited request", async () => {
    revealKolPoolContact
      .mockRejectedValueOnce({ status: 429 })
      .mockResolvedValueOnce({
        status: "revealed",
        kol_pool_id: 42,
        email: "manager@example.com",
        other_contacts: [],
        contact_masked: false,
      });
    render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("读取过于频繁");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("manager@example.com")).toBeTruthy();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(2);
  });

  it("uses the logged-in sender and never invents a personal From address", async () => {
    revealKolPoolContact.mockResolvedValue({
      status: "revealed",
      kol_pool_id: 42,
      email: "manager@example.com",
      other_contacts: [],
      contact_masked: false,
    });
    const view = render(
      <ContactModal
        item={item}
        apiToken="token"
        currentUser={{ id: 7, name: "Alice Ops", email: "alice@viltrox.com" }}
        onClose={vi.fn()}
      />,
    );
    expect(await screen.findByText("alice@viltrox.com")).toBeTruthy();
    expect(screen.getByDisplayValue(/I'm Alice Ops from Viltrox/)).toBeTruthy();
    expect(screen.queryByText("jianbo@viltrox.com")).toBeNull();
    view.unmount();

    render(<ContactModal item={{ ...item, id: 43 }} apiToken="token" onClose={vi.fn()} />);
    expect(await screen.findByText("未配置发件邮箱 · 复制后在邮箱客户端选择发件人")).toBeTruthy();
    expect(screen.getByDisplayValue(/I'm with Viltrox Partnerships/)).toBeTruthy();
  });

  it("aborts the request on close and does not retain plaintext across modal instances", async () => {
    revealKolPoolContact.mockResolvedValueOnce({
      status: "revealed",
      kol_pool_id: 42,
      email: "manager@example.com",
      other_contacts: [],
      contact_masked: false,
    });
    const first = render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);
    expect(await screen.findByText("manager@example.com")).toBeTruthy();
    const firstSignal = revealKolPoolContact.mock.calls[0][2].signal as AbortSignal;
    fireEvent.click(screen.getByRole("button", { name: "关闭合作邀请" }));
    first.unmount();
    expect(firstSignal.aborted).toBe(true);

    revealKolPoolContact.mockImplementationOnce(() => new Promise(() => undefined));
    render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);
    expect(screen.queryByText("manager@example.com")).toBeNull();
    expect(screen.getByText("正在授权读取联系方式…")).toBeTruthy();
  });

  it("never includes the revealed email in the AI optimization request", async () => {
    revealKolPoolContact.mockResolvedValue({
      status: "revealed",
      kol_pool_id: 42,
      email: "manager@example.com",
      other_contacts: [],
      contact_masked: false,
    });
    apiFetch.mockResolvedValue({ subject: "Polished", body: "Polished body" });
    render(<ContactModal item={item} apiToken="token" onClose={vi.fn()} />);
    expect(await screen.findByText("manager@example.com")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "AI 优化" }));
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const serializedRequest = JSON.stringify(apiFetch.mock.calls[0]);
    expect(serializedRequest).not.toContain("manager@example.com");
    expect(serializedRequest).not.toContain(item.handle);
    expect(serializedRequest).toContain("Future Shock Studios");
  });
});
