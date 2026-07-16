import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { VkpiProjectRow } from "../../vkpiTypes";
import { ProjectCampaignBoard } from "./ProjectCampaignBoard";
import { ProjectDetailView } from "./ProjectDetailView";

const project = {
  id: "42",
  kolName: "Creator One",
  kolHandle: "@creator",
  platform: "YouTube",
  campaign: "AF 85 上市",
  stage: "shipped",
  latestMessageAt: "2026-07-12T10:00:00Z",
  latestMessageSource: "DM",
  views: 1200,
  clicks: 30,
  orders: 2,
  gmv: 1000,
  cost: 200,
  roi: 5,
  ownerId: "staff-7",
  ownerName: "Alice",
  updatedAt: "2026-07-12T10:00:00Z",
} as VkpiProjectRow;

describe("Projects owner profile action", () => {
  it("campaign card renders static owner identity when no profile handler exists", () => {
    render(
      <ProjectCampaignBoard
        projects={[project]}
        viewMode="manager"
        onOpenProjectDetail={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("负责人 Alice").tagName).toBe("DIV");
    expect(screen.queryByRole("button", { name: "打开负责人 Alice" })).toBeNull();
  });

  it("campaign card preserves the real owner action when a profile handler exists", () => {
    const onOpenProjectDetail = vi.fn();
    const onOpenStaffProfile = vi.fn();
    render(
      <ProjectCampaignBoard
        projects={[project]}
        viewMode="manager"
        onOpenProjectDetail={onOpenProjectDetail}
        onOpenStaffProfile={onOpenStaffProfile}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "打开负责人 Alice" }));
    expect(onOpenStaffProfile).toHaveBeenCalledWith("staff-7", { name: "Alice", avatarUrl: undefined });
    expect(onOpenProjectDetail).not.toHaveBeenCalled();
  });

  it("project detail keeps the owner control disabled when no profile handler exists", () => {
    render(
      <ProjectDetailView
        project={project}
        projects={[project]}
        viewMode="manager"
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /负责人 Alice$/ })).toBeDisabled();
  });

  it("project detail preserves the real owner action when a profile handler exists", () => {
    const onOpenStaffProfile = vi.fn();
    render(
      <ProjectDetailView
        project={project}
        projects={[project]}
        viewMode="manager"
        onBack={vi.fn()}
        onOpenStaffProfile={onOpenStaffProfile}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /负责人 Alice$/ }));
    expect(onOpenStaffProfile).toHaveBeenCalledWith("staff-7", { name: "Alice", avatarUrl: undefined });
  });
});
