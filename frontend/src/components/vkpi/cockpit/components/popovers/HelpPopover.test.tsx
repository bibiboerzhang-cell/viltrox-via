import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", async () => {
  const actual = await vi.importActual<typeof import("framer-motion")>("framer-motion");
  return { ...actual, m: { div: (props: any) => <div {...props} style={props.style}>{props.children}</div> } };
});

vi.mock("../../../../../lib/buildInfo", () => ({
  frontendBuildInfo: { gitBranch: "test", gitSha: "abcdef123456", builtAt: "2026-09-02T01:02:03Z" },
  shortBuildSha: (value: string) => value.slice(0, 8),
}));

import { HelpPopover } from "./HelpPopover";

const t = (key: string) => key;
const HARDCODED_PALETTE = /\b(?:text|bg|border)-(?:white|black|slate|gray|amber|emerald|purple|blue)(?:-\d+)?(?:\/[\d.[\]]+)?\b/;

function renderHelp(props: Record<string, unknown> = {}) {
  return render(
    <HelpPopover onClose={() => {}} anchorRef={{ current: null }} t={t} onOpenShortcuts={() => {}} onOpenFeedback={() => {}} {...props} />,
  );
}

describe("HelpPopover (U-B2)", () => {
  it("shows the signed-in user instead of a hard-coded person and drops the fake verified badge", () => {
    renderHelp({ user: { name: "Tester", email: "tester@example.test", role: "member", avatar: "T" } });

    const block = screen.getByTestId("help-current-user");
    expect(block).toHaveTextContent("Tester");
    expect(block).toHaveTextContent("tester@example.test");
    expect(block).toHaveTextContent("成员");
    expect(document.body.textContent).not.toMatch(/张建波|BOBOBOBO|已认证|北美组|8582269427/);
    expect(screen.queryByTestId("help-support-contact")).toBeNull();
  });

  it("labels admins and hides the block entirely without a user", () => {
    const { unmount } = renderHelp({ user: { name: "Owner", email: "o@x.y", role: "owner" } });
    expect(screen.getByTestId("help-current-user")).toHaveTextContent("管理员");
    unmount();

    renderHelp({ user: null });
    expect(screen.queryByTestId("help-current-user")).toBeNull();
    expect(screen.queryByTestId("help-support-contact")).toBeNull();
  });

  it("renders a support contact only when explicitly configured", () => {
    renderHelp({ supportContact: { name: "Support desk", org: "Ops", note: "workdays 9-18" } });
    expect(screen.getByTestId("help-support-contact")).toHaveTextContent("Support desk");
  });

  it("reads the footer from build info and uses only theme tokens", () => {
    const { container } = renderHelp({ user: { name: "Tester", email: "t@x.y" } });
    expect(document.body).toHaveTextContent("V-KPI · abcdef12");
    expect(document.body).toHaveTextContent("更新 2026/09/02");
    expect(document.body.textContent).not.toContain("v6.14.2");
    for (const node of Array.from(document.body.querySelectorAll<HTMLElement>("[class]"))) {
      expect(node.getAttribute("class") || "", node.outerHTML.slice(0, 120)).not.toMatch(HARDCODED_PALETTE);
    }
    expect(container).toBeTruthy();
  });
});
