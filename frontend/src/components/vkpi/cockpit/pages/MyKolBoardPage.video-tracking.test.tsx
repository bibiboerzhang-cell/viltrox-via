import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  refreshMyKolVideoMetrics,
  trackMyKolExistingVideo,
  type KolLibraryRow,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import { KolDetailModal } from "./MyKolBoardPage.dialogs";
import { KolVideoSection } from "./MyKolBoardPage.libdetail";

function row(poolId: number, name: string, isShared = false): KolLibraryRow {
  return {
    poolId,
    name,
    handle: `@${name.toLowerCase()}`,
    platform: "youtube",
    followers: 100,
    fit: 70,
    avatarUrl: "",
    profileUrl: `https://youtube.com/@${name.toLowerCase()}`,
    country: "US",
    isShared,
    sharedByName: isShared ? "Owner" : "",
    projects: [],
    claim: null,
    email: "",
    createdAt: "2026-08-20T00:00:00Z",
  };
}

function video(evidenceId: number, poolId = 101): VkpiKolPoolVideoRow {
  return {
    evidence_id: evidenceId,
    id: evidenceId,
    kol_pool_id: poolId,
    platform: "youtube",
    media_kind: "video",
    title: `Video ${evidenceId}`,
    content_url: `https://www.youtube.com/watch?v=video${evidenceId}`,
    product_skus: ["AF-85-F14", "AF-35-F18"],
    tracking_status: "insufficient_history",
    sample_count: 1,
    attempt_count: 1,
  };
}

function installDialogRoutes(
  trackResult?: () => Promise<unknown>,
  videoRows?: (poolId: number) => VkpiKolPoolVideoRow[],
) {
  apiFetchMock.mockImplementation(async (path: unknown, init: RequestInit = {}) => {
    const value = String(path);
    if (/\/api\/admin\/vkpi\/my-kol\/\d+\/videos\/\d+\/refresh/.test(value) && init.method === "POST") {
      return { status: "queued", evidence_id: Number(value.match(/videos\/(\d+)\/refresh/)?.[1]), job_id: 73 };
    }
    if (value === "/api/admin/vkpi/my-kol/101/videos" && init.method === "POST") {
      if (trackResult) return trackResult();
      return { status: "queued", evidence_id: 901, job_id: 71, product_skus: ["AF-85-F14"] };
    }
    if (value === "/api/admin/vkpi/my-kol/102/videos" && init.method === "POST") {
      return { status: "queued", evidence_id: 902, job_id: 72, product_skus: [] };
    }
    if (value === "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue" && init.method === "POST") {
      return { status: "queued", job_id: 88 };
    }
    const videosMatch = value.match(/\/api\/admin\/vkpi\/(?:kol-pool|my-kol)\/(\d+)\/videos(?:\?|$)/);
    if (videosMatch) {
      const poolId = Number(videosMatch[1]);
      const items = videoRows ? videoRows(poolId) : [video(poolId === 101 ? 901 : 902, poolId)];
      return { items, total: items.length, kol_pool_id: poolId };
    }
    if (/\/api\/admin\/vkpi\/goaffpro\/kol\/\d+\/link/.test(value)) return { linked: false };
    const viewerMatch = value.match(/\/api\/admin\/vkpi\/my-kol\/(\d+)\/viewer-context/);
    if (viewerMatch) {
      const canWrite = Number(viewerMatch[1]) !== 102;
      return {
        claim: null,
        paid_actions: {
          can_run_paid_actions: canWrite,
          reason: canWrite ? "owned_favorite" : "my_kol_paid_action_write_forbidden",
        },
      };
    }
    throw new Error(`unexpected apiFetch: ${value}`);
  });
}

function renderDetail(index = 0, rows = [row(101, "Alpha"), row(102, "Beta", true)]) {
  return render(
    <KolDetailModal
      apiToken="token"
      rows={rows}
      index={index}
      onNav={vi.fn()}
      onClose={vi.fn()}
      projects={[]}
    />,
  );
}

beforeEach(() => {
  apiFetchMock.mockReset();
  window.sessionStorage.clear();
});

describe("MY KOL video tracking service contract", () => {
  it("sends the existing URL and bounded SKU list, then queues one evidence refresh", async () => {
    apiFetchMock.mockResolvedValue({ status: "queued", evidence_id: 901 });

    await trackMyKolExistingVideo("token", 101, {
      contentUrl: "https://www.youtube.com/watch?v=abc",
      productSkus: ["AF-85-F14", "AF-35-F18"],
    });
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/admin/vkpi/my-kol/101/videos",
      {
        method: "POST",
        body: JSON.stringify({
          content_url: "https://www.youtube.com/watch?v=abc",
          product_skus: ["AF-85-F14", "AF-35-F18"],
        }),
      },
      "token",
    );

    await refreshMyKolVideoMetrics("token", 101, 901);
    expect(apiFetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/vkpi/my-kol/101/videos/901/refresh",
      { method: "POST" },
      "token",
    );
  });
});

describe("KolVideoSection product and refresh controls", () => {
  it("shows server-returned SKU chips and an honest queued refresh state", () => {
    const onRefresh = vi.fn();
    const props = {
      videos: [video(901)],
      queuedEvidence: new Set<number>(),
      busyKeys: new Set<string>(),
      onEnqueueOne: vi.fn(),
      onRefreshMetrics: onRefresh,
    };
    const { rerender } = render(<KolVideoSection {...props} />);

    expect(screen.getByText("AF-85-F14")).toBeInTheDocument();
    expect(screen.getByText("AF-35-F18")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新指标" }));
    expect(onRefresh).toHaveBeenCalledWith(expect.objectContaining({ evidence_id: 901 }));

    rerender(<KolVideoSection {...props} queuedRefreshEvidence={new Set([901])} />);
    expect(screen.getByRole("button", { name: "指标刷新已排队" })).toBeDisabled();
    expect(screen.queryByText(/实时完成/)).toBeNull();
  });
});

describe("KolDetailModal existing-video tracking", () => {
  it("keeps the submit target disabled until a URL exists and exposes a direct account-crawl recovery path", async () => {
    installDialogRoutes();
    renderDetail();
    await screen.findByText("Video 901");

    const picker = screen.getByLabelText("从已采集内容选择视频");
    const submit = screen.getByRole("button", { name: "追踪并排队刷新" });
    expect(picker).toHaveClass("min-h-9", "text-[11.5px]");
    expect(submit).toBeDisabled();
    expect(submit).toHaveClass("min-h-9");
    fireEvent.change(picker, { target: { value: "https://www.youtube.com/watch?v=video901" } });
    expect(screen.getByLabelText("已有视频 URL")).toHaveValue("https://www.youtube.com/watch?v=video901");
    expect(submit).toBeEnabled();
    expect(screen.getByText(/找不到目标视频？先补采账号内容/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "账号补采 / 深爬" }));
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue",
        expect.objectContaining({ method: "POST" }),
        "token",
      );
    });
    expect(await screen.findByText(/已入队深爬/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开 KOL 档案" })).toHaveClass("min-h-9");
  });

  it("shows the account recovery entry when collected evidence has no stored URL", async () => {
    installDialogRoutes(undefined, (poolId) => [{ ...video(901, poolId), content_url: "" }]);
    renderDetail();
    await screen.findByText("Video 901");

    expect(screen.queryByLabelText("从已采集内容选择视频")).toBeNull();
    expect(screen.getByText(/当前没有带 URL 的已采集视频/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "账号补采 / 深爬" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "打开 KOL 档案" })).toBeEnabled();
  });

  it("keeps deep crawl unavailable without a profile URL but leaves the profile repair route reachable", async () => {
    installDialogRoutes(undefined, (poolId) => [{ ...video(901, poolId), content_url: "" }]);
    const openProfile = vi.fn();
    window.addEventListener("vkpi:open-kol-profile", openProfile);
    renderDetail(0, [{ ...row(101, "Alpha"), profileUrl: "" }]);
    await screen.findByText("Video 901");

    expect(screen.getByText(/缺少主页链接，请先打开 KOL 档案/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "账号补采 / 深爬" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "打开 KOL 档案" }));
    expect(openProfile).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem("vkpi:kol-profile-id")).toBe("101");
    window.removeEventListener("vkpi:open-kol-profile", openProfile);
  });

  it("disables both paid account-crawl entries for a shared read-only KOL", async () => {
    installDialogRoutes();
    renderDetail(1);
    await screen.findByText("Video 902");

    expect(screen.getByText(/共享 KOL 为只读，不能发起会产生外部采集成本/)).toBeInTheDocument();
    const recovery = screen.getByRole("button", { name: "账号补采 / 深爬" });
    const footer = screen.getByRole("button", { name: "账号分析 · 补采" });
    expect(recovery).toBeDisabled();
    expect(footer).toBeDisabled();
    fireEvent.click(recovery);
    fireEvent.click(footer);
    expect(
      apiFetchMock.mock.calls.some(([path]) => String(path) === "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue"),
    ).toBe(false);
  });

  it("queues a card-level metric refresh and labels only the queued state", async () => {
    installDialogRoutes();
    renderDetail();
    await screen.findByText("Video 901");

    fireEvent.click(screen.getByRole("button", { name: "刷新指标" }));
    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/api/admin/vkpi/my-kol/101/videos/901/refresh",
        { method: "POST" },
        "token",
      );
    });
    expect(await screen.findByRole("button", { name: "指标刷新已排队" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("指标刷新已排队（#901）");
  });

  it("submits a deduplicated comma-separated SKU list and refreshes the evidence list", async () => {
    installDialogRoutes();
    renderDetail();
    await screen.findByText("Video 901");
    const readsBefore = apiFetchMock.mock.calls.filter(([path]) => String(path).includes("/my-kol/101/videos?")).length;

    fireEvent.change(screen.getByLabelText("已有视频 URL"), { target: { value: "https://www.youtube.com/watch?v=video901" } });
    fireEvent.change(screen.getByLabelText("关联产品 SKU"), { target: { value: "AF-85-F14， AF-35-F18,AF-85-F14" } });
    fireEvent.click(screen.getByRole("button", { name: "追踪并排队刷新" }));

    await waitFor(() => {
      const call = apiFetchMock.mock.calls.find(([path, init]) => String(path) === "/api/admin/vkpi/my-kol/101/videos" && (init as RequestInit)?.method === "POST");
      expect(JSON.parse(String((call?.[1] as RequestInit)?.body))).toEqual({
        content_url: "https://www.youtube.com/watch?v=video901",
        product_skus: ["AF-85-F14", "AF-35-F18"],
      });
    });
    expect((await screen.findAllByText(/指标刷新已排队/)).length).toBeGreaterThan(0);
    await waitFor(() => {
      const readsAfter = apiFetchMock.mock.calls.filter(([path]) => String(path).includes("/my-kol/101/videos?")).length;
      expect(readsAfter).toBeGreaterThan(readsBefore);
    });
  });

  it("maps an unseen URL to the actionable account crawl/deep-crawl message", async () => {
    installDialogRoutes(async () => {
      throw Object.assign(new Error("new_video_target_resolution_required"), {
        detail: "new_video_target_resolution_required",
        status: 422,
      });
    });
    renderDetail();
    await screen.findByText("Video 901");
    fireEvent.change(screen.getByLabelText("已有视频 URL"), { target: { value: "https://youtu.be/unseen" } });
    fireEvent.click(screen.getByRole("button", { name: "追踪并排队刷新" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前仅支持已采集视频，请先账号补采/深爬后重试");
  });

  it("disables shared paid actions before any write request", async () => {
    installDialogRoutes();
    renderDetail(1);
    await screen.findByText("Video 902");
    expect(screen.getByLabelText("已有视频 URL")).toBeDisabled();
    expect(screen.getByRole("button", { name: "追踪并排队刷新" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "刷新指标" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /视频深析入队/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "采集评论" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "受众画像" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("共享 KOL 仅可查看");
    expect(
      apiFetchMock.mock.calls.some(
        ([path, init]) => String(path).includes("/my-kol/102/") && (init as RequestInit)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("drops a late Alpha response after navigation to Beta", async () => {
    let resolveTrack: (value: unknown) => void = () => undefined;
    const pending = new Promise((resolve) => { resolveTrack = resolve; });
    installDialogRoutes(() => pending);
    const rendered = renderDetail(0);
    await screen.findByText("Video 901");
    fireEvent.change(screen.getByLabelText("已有视频 URL"), { target: { value: "https://www.youtube.com/watch?v=video901" } });
    fireEvent.click(screen.getByRole("button", { name: "追踪并排队刷新" }));

    rendered.rerender(
      <KolDetailModal apiToken="token" rows={[row(101, "Alpha"), row(102, "Beta", true)]} index={1} onNav={vi.fn()} onClose={vi.fn()} projects={[]} />,
    );
    await screen.findByText("Video 902");
    await act(async () => { resolveTrack({ status: "queued", evidence_id: 901, job_id: 71 }); await pending; });

    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.queryByText(/evidence #901/)).toBeNull();
    expect(screen.getByLabelText("已有视频 URL")).toHaveValue("");
  });
});
