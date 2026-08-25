import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KOLDrawerContactAndVideos, KOLDrawerHeader } from "./KOLDetailDrawerSections";
import { audienceMemberDisplayName } from "./KOLDetailDrawerSections.More";
import { KolPoolAllModal } from "./modals/KolPoolAllModal";
import { projectKolDisplayName } from "./modals/ProjectDetailModal";
import { buildItemOptions } from "../CockpitApp.helpers";
import { normalizeTopMovers } from "../normalizers";
import {
  launchMemberDisplayName,
  launchMemberPublicHandle,
} from "../pages/LaunchPadBoardPage.modules";
import { IdentityBody } from "../pages/KolProfileBoardPage.modules";
import {
  goaffproKolDisplayName,
  goaffproKolPublicHandle,
} from "../pages/ShopifyBoardPage.modules";

const machineId = "UC0123456789abcdefghij";

describe("human KOL identity on major pool surfaces", () => {
  it("drawer header suppresses an opaque channel id", () => {
    render(
      <KOLDrawerHeader
        item={{ display_name: "Future Shock Studios", handle: machineId, platform: "youtube" }}
        devices={{ has_viltrox: false, competitor_brands: [] }}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Future Shock Studios" })).toBeTruthy();
    expect(screen.queryByText(machineId)).toBeNull();
  });

  it("all-pool modal is named, focuses its close action, and uses Creator plus YouTube when no human name exists", async () => {
    render(
      <KolPoolAllModal
        items={[{ id: 42, display_name: "", handle: machineId, platform: "youtube" }]}
        onClose={vi.fn()}
        onRowClick={vi.fn()}
      />,
    );

    expect(screen.getByText("Creator")).toBeTruthy();
    expect(screen.getAllByText(/youtube/i).length).toBeGreaterThan(0);
    expect(screen.queryByText(machineId)).toBeNull();
    expect(screen.getByRole("dialog", { name: "全部 KOL" })).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus());
  });

  it("keeps the opaque channel id in the profile href but not in visible drawer text", () => {
    const profileUrl = `https://www.youtube.com/channel/${machineId}`;
    render(
      <KOLDrawerContactAndVideos
        item={{
          display_name: "Future Shock Studios",
          handle: machineId,
          channel_id: machineId,
          platform: "youtube",
          profile_url: profileUrl,
          contact_masked: false,
        }}
        representativeVideos={[]}
        onOpenVideo={vi.fn()}
      />,
    );

    const link = screen.getByRole("link", { name: "打开 YouTube 主页" });
    expect(link).toHaveAttribute("href", profileUrl);
    expect(document.body.textContent).not.toContain(machineId);
  });

  it("uses a human link label on the KOL profile board", () => {
    const profileUrl = `https://www.youtube.com/channel/${machineId}`;
    render(
      <IdentityBody
        item={{
          display_name: "Future Shock Studios",
          handle: machineId,
          channel_id: machineId,
          platform: "youtube",
          profile_url: profileUrl,
        }}
        signatureLine="待分析"
        coopLabel="未记录"
        onOpenDrawer={vi.fn()}
      />,
    );

    const links = screen.getAllByRole("link", { name: /打开 YouTube 主页/ });
    expect(links.length).toBeGreaterThan(0);
    expect(links.every((link) => link.getAttribute("href") === profileUrl)).toBe(true);
    expect(document.body.textContent).not.toContain(machineId);
  });

  it("suppresses opaque ids across launch, affiliate, project, map, and mover adapters", () => {
    const launchMember = { displayName: machineId, handle: machineId, platform: "youtube" } as any;
    const affiliateRow = { kol_name: machineId, kol_handle: machineId, kol_platform: "youtube" } as any;

    expect(launchMemberDisplayName(launchMember)).toBe("Creator");
    expect(launchMemberPublicHandle(launchMember)).toBe("");
    expect(goaffproKolDisplayName(affiliateRow)).toBe("Creator");
    expect(goaffproKolPublicHandle(affiliateRow)).toBe("");
    expect(projectKolDisplayName({ display_name: "", handle: machineId, platform: "youtube" })).toBe("Creator");
    expect(audienceMemberDisplayName({ handle: machineId, channel_id: machineId })).toBe("评论者");

    const options = buildItemOptions({
      country: "US",
      city: "Austin",
      hierarchy: {
        US: {
          cities: {
            Austin: {
              kols: [{ display_name: "", handle: machineId, platform: "youtube" }],
            },
          },
        },
      } as any,
    });
    expect(options[1]).toEqual(expect.objectContaining({ key: machineId, label: "Creator" }));

    const [serverMover] = normalizeTopMovers([], {
      available: true,
      movers: [{ kol_pool_id: 42, name: machineId, handle: machineId, platform: "youtube", delta: 1 }],
    });
    const [fallbackMover] = normalizeTopMovers([
      { id: 42, display_name: "", handle: machineId, platform: "youtube", v6_fit: 80 },
    ]);
    expect(serverMover.handle).toBe("KOL 1");
    expect(fallbackMover.handle).toBe("KOL 1");
    expect(JSON.stringify({ options, serverMover, fallbackMover })).not.toContain(`"label":"${machineId}"`);
  });

  it("rejects a channel id embedded inside a display-name URL", () => {
    const urlName = `https://youtube.com/channel/${machineId}`;
    expect(projectKolDisplayName({ display_name: urlName, handle: machineId, platform: "youtube" })).toBe("Creator");
  });
});
