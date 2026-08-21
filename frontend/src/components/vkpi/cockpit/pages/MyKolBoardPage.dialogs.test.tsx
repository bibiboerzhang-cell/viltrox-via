import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { KolLibraryRow } from "../../../../services/vkpi/myKolBoard-api";
import { KolLibraryListModal, KolRowLine } from "./MyKolBoardPage.dialogs";

const refreshAudienceStatsMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  refreshAudienceStats: (...args: unknown[]) => refreshAudienceStatsMock(...args),
}));

function libraryRow(overrides: Partial<KolLibraryRow> = {}): KolLibraryRow {
  return {
    poolId: 7,
    name: "Matthew",
    handle: "matthewjgl",
    platform: "tiktok",
    followers: 2_300_000,
    fit: 79,
    avatarUrl: "",
    profileUrl: "https://www.tiktok.com/@matthewjgl",
    country: "US",
    isShared: false,
    sharedByName: "",
    projects: [],
    claim: null,
    email: "",
    createdAt: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

describe("KolRowLine avatar request safety", () => {
  it("routes a TikTok CDN avatar through the authenticated same-origin proxy", () => {
    const raw = "https://p19-common-sign.tiktokcdn-us.com/tos-useast5-avt/photo.jpeg?x-signature=secret";
    const { container } = render(<KolRowLine row={libraryRow({ avatarUrl: raw })} index={0} onOpen={vi.fn()} />);

    const image = container.querySelector("img");
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toBe(`/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`);
    expect(image?.getAttribute("src")).not.toBe(raw);
  });

  it("routes a European TikTok CDN avatar through the same-origin proxy", () => {
    const raw = "https://p16-sign-va.tiktokcdn-eu.com/tos-maliva-avt-0068/avatar.jpeg";
    const { container } = render(
      <KolRowLine row={libraryRow({ avatarUrl: raw })} index={0} onOpen={vi.fn()} />,
    );

    const image = container.querySelector("img");
    expect(image?.getAttribute("src")).toBe(`/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`);
  });

  it("replaces a rejected proxy response or missing URL with an honest initial", () => {
    const { container, rerender } = render(
      <KolRowLine
        row={libraryRow({ avatarUrl: "https://p19-common-sign.tiktokcdn-us.com/avatar.jpeg" })}
        index={0}
        onOpen={vi.fn()}
      />,
    );
    const failedImage = container.querySelector("img");
    expect(failedImage).not.toBeNull();
    fireEvent.error(failedImage as HTMLImageElement);
    expect(screen.getByLabelText("Matthew 头像暂不可用")).toHaveTextContent("M");

    rerender(<KolRowLine row={libraryRow({ avatarUrl: "" })} index={0} onOpen={vi.fn()} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByLabelText("Matthew 头像暂不可用")).toHaveTextContent("M");
  });

  it("rejects a 1x1 upstream fallback instead of treating it as a real avatar", () => {
    const { container } = render(
      <KolRowLine
        row={libraryRow({ avatarUrl: "https://p19-common-sign.tiktokcdn-us.com/avatar.jpeg" })}
        index={0}
        onOpen={vi.fn()}
      />,
    );
    const image = container.querySelector("img") as HTMLImageElement;
    Object.defineProperty(image, "naturalWidth", { configurable: true, value: 1 });
    Object.defineProperty(image, "naturalHeight", { configurable: true, value: 1 });
    fireEvent.load(image);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByLabelText("Matthew 头像暂不可用")).toHaveTextContent("M");
  });
});

describe("KolLibraryListModal shared paid-action scope", () => {
  it("skips shared rows in batch audience refresh while keeping owned rows", async () => {
    refreshAudienceStatsMock.mockReset();
    refreshAudienceStatsMock.mockResolvedValue({ status: "queued", job_id: 91 });
    render(
      <KolLibraryListModal
        apiToken="token"
        rows={[
          libraryRow({ poolId: 7, name: "Owned", isShared: false }),
          libraryRow({ poolId: 8, name: "Shared", isShared: true, sharedByName: "Owner" }),
        ]}
        totalAll={2}
        filter={{ query: "", platform: "", vOnly: false }}
        onFilter={vi.fn()}
        platformOptions={[]}
        vKolCount={null}
        projects={[]}
        onOpenDetail={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("勾选 Owned"));
    fireEvent.click(screen.getByLabelText("勾选 Shared"));
    fireEvent.click(screen.getByRole("button", { name: "批量受众画像" }));

    await waitFor(() => expect(refreshAudienceStatsMock).toHaveBeenCalledTimes(1));
    expect(refreshAudienceStatsMock).toHaveBeenCalledWith("token", 7);
    expect(screen.getByRole("status")).toHaveTextContent("跳过共享只读 1 个");
  });
});
