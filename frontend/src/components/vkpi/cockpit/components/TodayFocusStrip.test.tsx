import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const listActionInbox = vi.fn();
const apiFetch = vi.fn();

vi.mock("../../../../services/vkpi/actionInbox-api", () => ({
  listActionInbox: (...args: unknown[]) => listActionInbox(...args),
}));

vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

import { TodayFocusStrip } from "./TodayFocusStrip";

const ACTIONS = [
  { id: 1, category: "failed_retry", priority: "high", title: "重试失败的深析" },
  { id: 2, category: "content_candidate", priority: "medium", title: "确认新视频证据" },
];

beforeEach(() => {
  listActionInbox.mockReset();
  apiFetch.mockReset();
});

describe("TodayFocusStrip 紧凑焦点摘要", () => {
  it("优先使用新契约精确总数,并可验证待办/晨报跳转 callback", async () => {
    listActionInbox.mockResolvedValue({
      items: ACTIONS,
      count: 2,
      total_count: 126,
      available: true,
    });
    apiFetch.mockResolvedValue({ status: "ready", headline: "昨晚完成 8 件，0 失败" });
    const onJumpToInbox = vi.fn();
    const onJumpToBrief = vi.fn();

    render(
      <TodayFocusStrip
        apiToken="tok"
        onJumpToInbox={onJumpToInbox}
        onJumpToBrief={onJumpToBrief}
      />,
    );

    expect(await screen.findByText("待审 126")).toHaveAttribute("data-count-kind", "exact");
    expect(screen.getByText("优先待办 · 展示 2")).toBeInTheDocument();
    expect(screen.getByText("昨晚完成 8 件，0 失败")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "待审 126 · 待执行 126 · 待对账 126,点击查看全部" }));
    fireEvent.click(screen.getByRole("button", { name: "查看待办:重试失败的深析" }));
    fireEvent.click(screen.getByRole("button", { name: "查看夜班晨报" }));

    expect(onJumpToInbox).toHaveBeenCalledTimes(2);
    expect(onJumpToBrief).toHaveBeenCalledTimes(1);
  });

  it("兼容旧 count 截断口径:返回到 limit 时诚实显示 20+", async () => {
    listActionInbox.mockResolvedValue({
      items: Array.from({ length: 20 }, (_, index) => ({
        id: index + 1,
        category: "deep_missing",
        priority: "medium",
        title: `待办 ${index + 1}`,
      })),
      count: 20,
      available: true,
    });
    apiFetch.mockResolvedValue({ status: "ready", headline: "" });

    render(<TodayFocusStrip apiToken="tok" />);

    expect(await screen.findByText("待审 20+")).toHaveAttribute("data-count-kind", "lower-bound");
    expect(screen.getByText("优先待办 · 展示 3")).toBeInTheDocument();
  });

  it("两路独立收口:晨报未返回时先展示待办", async () => {
    let resolveBrief!: (value: unknown) => void;
    listActionInbox.mockResolvedValue({ items: ACTIONS.slice(0, 1), count: 1, available: true });
    apiFetch.mockReturnValue(new Promise((resolve) => { resolveBrief = resolve; }));

    const { unmount } = render(<TodayFocusStrip apiToken="tok" />);

    expect(await screen.findByText("待审 1")).toBeInTheDocument();
    expect(screen.getByText("重试失败的深析")).toBeInTheDocument();
    expect(screen.getByText("读取晨报")).toBeInTheDocument();

    resolveBrief({ status: "ready", headline: "晨报稍后到达" });
    expect(await screen.findByText("晨报稍后到达")).toBeInTheDocument();
    unmount();
  });

  it("一路失败不吞掉另一路真实结果", async () => {
    listActionInbox.mockRejectedValue(new Error("数据库读取失败"));
    apiFetch.mockResolvedValue({ status: "ready", headline: "窗口内无新任务" });

    render(<TodayFocusStrip apiToken="tok" />);

    expect(await screen.findByText(/建议源异常/)).toHaveTextContent("数据库读取失败");
    expect(screen.getByText("窗口内无新任务")).toBeInTheDocument();
    expect(screen.getByLabelText("今日焦点")).toHaveAttribute("data-state", "degraded");
  });

  it("失败重试 chips 按 job_type 聚合成一枚计数 chip,点击仍可跳转", async () => {
    listActionInbox.mockResolvedValue({
      items: [
        { id: 11, category: "failed_retry", priority: "high", title: "失败任务待重试 · video", payload_json: { job_type: "video" } },
        { id: 12, category: "failed_retry", priority: "high", title: "失败任务待重试 · video", payload_json: { job_type: "video" } },
        { id: 13, category: "failed_retry", priority: "high", title: "失败任务待重试 · kol_profile_deep_crawl", payload_json: { job_type: "kol_profile_deep_crawl" } },
        { id: 14, category: "content_candidate", priority: "medium", title: "确认新视频证据" },
      ],
      count: 4,
      available: true,
    });
    apiFetch.mockResolvedValue({ status: "ready", headline: "" });
    const onJumpToInbox = vi.fn();

    render(<TodayFocusStrip apiToken="tok" onJumpToInbox={onJumpToInbox} />);

    expect(await screen.findByText("video ×2")).toBeInTheDocument();
    expect(screen.getByText("kol_profile_deep_crawl")).toBeInTheDocument();
    // 同 job_type 不再逐条铺 chip:三条 failed_retry 聚合成两枚 + 其他类别一枚 = 展示 3。
    expect(screen.getByText("优先待办 · 展示 3")).toBeInTheDocument();
    expect(screen.queryAllByText("失败任务待重试 · video")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "查看待办:video ×2" }));
    expect(onJumpToInbox).toHaveBeenCalledTimes(1);
  });

  it("无 token 时不渲染也不发请求", async () => {
    const { container } = render(<TodayFocusStrip apiToken="" />);
    expect(container.textContent).toBe("");
    await waitFor(() => {
      expect(listActionInbox).not.toHaveBeenCalled();
      expect(apiFetch).not.toHaveBeenCalled();
    });
  });
});
