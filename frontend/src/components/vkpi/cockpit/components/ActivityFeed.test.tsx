import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// U4:ActivityFeed 思考流冒烟。seam = ../../../../services/http 的 apiFetch(hermetic)。
// 覆盖:条目渲染 + 滚动容器(hover 暂停挂点)存在、空态、失败态、无 token。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
  buildApiUrl: (p: string) => p,
}));

import { ActivityFeed } from "./ActivityFeed";

beforeEach(() => {
  apiFetch.mockReset();
});

const ITEMS = {
  items: [
    { ts: "2026-07-07T01:00:00Z", kind: "job", icon: "video", text: "视频分析任务完成", link: null },
    { ts: "2026-07-07T00:30:00Z", kind: "alert", icon: "alert-triangle", text: "信号预警:掉粉", link: "/cockpit/alerts" },
  ],
};

describe("ActivityFeed 思考流", () => {
  it("有数据 → 渲染条目 + 条数徽标 + 可 hover 暂停的滚动容器", async () => {
    apiFetch.mockResolvedValue(ITEMS);
    const { unmount } = render(<ActivityFeed apiToken="tok" />);

    expect(await screen.findByText("视频分析任务完成")).toBeInTheDocument();
    expect(screen.getByText("信号预警:掉粉")).toBeInTheDocument();
    expect(screen.getByText("2 条")).toBeInTheDocument();

    // 动画载体断言:滚动容器存在(自动滚动 + hover 暂停都挂在它身上)。
    const scroller = screen.getByTestId("activity-scroll");
    expect(scroller).toBeInTheDocument();
    // hover 进出不崩(暂停/恢复路径)。
    fireEvent.mouseEnter(scroller);
    fireEvent.mouseLeave(scroller);

    // 请求走正确端点 + token。
    const [path, , token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/activity/recent?limit=30");
    expect(token).toBe("tok");
    unmount(); // 清掉 10s 轮询 interval
  });

  it("空数据 → 「暂无思考流」诚实空态,不装 live", async () => {
    apiFetch.mockResolvedValue({ items: [] });
    const { unmount } = render(<ActivityFeed apiToken="tok" />);
    expect(await screen.findByText("暂无思考流")).toBeInTheDocument();
    unmount();
  });

  it("接口失败且从未成功 → 「待接入」", async () => {
    apiFetch.mockRejectedValue(new Error("net down"));
    const { unmount } = render(<ActivityFeed apiToken="tok" />);
    expect(await screen.findByText("待接入")).toBeInTheDocument();
    unmount();
  });

  it("无 token → 「待接入」且不发请求", async () => {
    const { unmount } = render(<ActivityFeed />);
    await waitFor(() => expect(screen.getByText("待接入")).toBeInTheDocument());
    expect(apiFetch).not.toHaveBeenCalled();
    unmount();
  });
});
