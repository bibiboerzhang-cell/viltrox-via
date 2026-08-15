import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const revealKolPoolContact = vi.fn();

vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  revealKolPoolContact: (...args: unknown[]) => revealKolPoolContact(...args),
}));

import { useKolContactState } from "./useKolContactState";
import type { ContactPurpose, ContactState } from "./kolContacts";

function Probe({
  apiToken = "token",
  kolPoolId = 42,
  purpose = "kol_detail_view",
  initialState = null,
}: {
  apiToken?: string;
  kolPoolId?: number;
  purpose?: ContactPurpose;
  initialState?: ContactState | null;
}) {
  const { state, clear } = useKolContactState({ apiToken, kolPoolId, purpose, initialState });
  return (
    <div>
      <span>{state.status}</span>
      {state.contacts.map((contact) => <span key={`${contact.type}:${contact.value}`}>{contact.value}</span>)}
      <button type="button" onClick={clear}>clear</button>
    </div>
  );
}

function auditedState(value: string, purpose: ContactPurpose): ContactState {
  return {
    status: "full",
    contacts: [{ type: "email", label: "邮箱", value, action: "email", actionLabel: "发送邮件", masked: false }],
    auditedPurpose: purpose,
    auditedKolPoolId: "42",
  };
}

beforeEach(() => {
  revealKolPoolContact.mockReset().mockResolvedValue({
    status: "full",
    kol_pool_id: 42,
    contact_masked: false,
    contacts: [{ type: "email", value: "manager@example.com" }],
  });
});

describe("useKolContactState", () => {
  it("reuses a terminal initial state only for the same audited purpose and KOL", () => {
    render(
      <Probe
        purpose="compose_outreach"
        initialState={auditedState("same-purpose@example.com", "compose_outreach")}
      />,
    );

    expect(screen.getByText("same-purpose@example.com")).toBeInTheDocument();
    expect(revealKolPoolContact).not.toHaveBeenCalled();
  });

  it("drops cross-purpose plaintext and requests a new audited projection", async () => {
    render(
      <Probe
        purpose="compose_outreach"
        initialState={auditedState("drawer-only@example.com", "kol_detail_view")}
      />,
    );

    expect(screen.queryByText("drawer-only@example.com")).toBeNull();
    expect(await screen.findByText("manager@example.com")).toBeInTheDocument();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
    expect(revealKolPoolContact).toHaveBeenCalledWith("token", 42, {
      signal: expect.any(AbortSignal),
      purpose: "compose_outreach",
    });
  });

  it("performs one audited request when StrictMode replays effects", async () => {
    render(
      <React.StrictMode>
        <Probe />
      </React.StrictMode>,
    );

    expect(await screen.findByText("manager@example.com")).toBeInTheDocument();
    expect(revealKolPoolContact).toHaveBeenCalledTimes(1);
    expect(revealKolPoolContact).toHaveBeenCalledWith("token", 42, {
      signal: expect.any(AbortSignal),
      purpose: "kol_detail_view",
    });
  });

  it("drops plaintext and aborts the surface request when cleared", async () => {
    render(<Probe />);
    expect(await screen.findByText("manager@example.com")).toBeInTheDocument();
    const signal = revealKolPoolContact.mock.calls[0][2].signal as AbortSignal;

    fireEvent.click(screen.getByRole("button", { name: "clear" }));

    await waitFor(() => expect(screen.queryByText("manager@example.com")).toBeNull());
    expect(screen.getByText("restricted")).toBeInTheDocument();
    expect(signal.aborted).toBe(true);
  });
});
