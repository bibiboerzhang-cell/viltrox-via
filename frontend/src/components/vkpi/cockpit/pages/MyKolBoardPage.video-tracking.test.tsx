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

function installDialogRoutes(trackResult?: () => Promise<unknown>) {
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
    const videosMatch = value.match(/\/api\/admin\/vkpi\/kol-pool\/(\d+)\/videos/);
    if (videosMatch) {
      const poolId = Number(videosMatch[1]);
      return { items: [video(poolId === 101 ? 901 : 902, poolId)], total: 1, kol_pool_id: poolId };
    }
    if (/\/api\/admin\/vkpi\/goaffpro\/kol\/\d+\/link/.test(value)) return { linked: false };
    if (/\/api\/admin\/vkpi\/my-kol\/\d+\/viewer-context/.test(value)) return { claim: null };
    throw new Error(`unexpected apiFetch: ${value}`);
  });
}

function renderDetail(index = 0) {
  const rows = [row(101, "Alpha"), row(102, "Beta", true)];
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
    const readsBefore = apiFetchMock.mock.calls.filter(([path]) => String(path).includes("/kol-pool/101/videos")).length;

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
      const readsAfter = apiFetchMock.mock.calls.filter(([path]) => String(path).includes("/kol-pool/101/videos")).length;
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

  it("shows a server permission rejection for a shared read-only KOL", async () => {
    installDialogRoutes();
    apiFetchMock.mockImplementation(async (path: unknown, init: RequestInit = {}) => {
      const value = String(path);
      if (value === "/api/admin/vkpi/my-kol/102/videos" && init.method === "POST") {
        throw Object.assign(new Error("kol_pool_not_writable"), { detail: "kol_pool_not_writable", status: 403 });
      }
      const videosMatch = value.match(/\/api\/admin\/vkpi\/kol-pool\/(\d+)\/videos/);
      if (videosMatch) return { items: [video(902, 102)], total: 1, kol_pool_id: 102 };
      if (/\/api\/admin\/vkpi\/goaffpro\/kol\/\d+\/link/.test(value)) return { linked: false };
      if (/\/api\/admin\/vkpi\/my-kol\/\d+\/viewer-context/.test(value)) return { claim: null };
      throw new Error(`unexpected apiFetch: ${value}`);
    });
    renderDetail(1);
    await screen.findByText("Video 902");
    fireEvent.change(screen.getByLabelText("已有视频 URL"), { target: { value: "https://www.youtube.com/watch?v=video902" } });
    fireEvent.click(screen.getByRole("button", { name: "追踪并排队刷新" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("服务端拒绝写入");
    expect(screen.queryByText(/指标刷新已排队（evidence/)).toBeNull();
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
