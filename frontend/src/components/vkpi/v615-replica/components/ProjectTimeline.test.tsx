import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// W5 recon: ProjectTimeline 渲染 smoke。seam = ../../../../services/http 的 apiFetch。
// 覆盖:降级骨架(无 token/id)、后端 stages 渲染、错误态。hermetic(不打后端)。
const apiFetch = vi.fn();
vi.mock("../../../../services/http", () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
}));

import { ProjectTimeline } from "./ProjectTimeline";

beforeEach(() => {
  apiFetch.mockReset();
});

describe("ProjectTimeline 渲染 smoke", () => {
  it("无 token/projectId → 降级骨架(canonical 阶段顺序),不发请求", async () => {
    render(<ProjectTimeline apiToken="" projectId="" />);
    // 降级 banner
    expect(await screen.findByText(/履约阶段骨架/)).toBeInTheDocument();
    // canonical 阶段标签存在(首尾各取一)
    expect(screen.getByText("发现")).toBeInTheDocument();
    expect(screen.getByText("已关闭")).toBeInTheDocument();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("有后端 stages → 按 index 有序渲染 + 当前阶段 banner", async () => {
    apiFetch.mockResolvedValue({
      current_stage: "shipped",
      stages: [
        { key: "contacted", label_zh: "已联系", index: 1, reached: true, at: null, counts: {} },
        { key: "discovered", label_zh: "发现", index: 0, reached: true, at: null, counts: {} },
        { key: "shipped", label_zh: "已寄样", index: 2, reached: false, at: null, counts: {} },
      ],
    });
    render(<ProjectTimeline apiToken="tok" projectId="42" />);

    expect(await screen.findByText(/履约时间线/)).toBeInTheDocument();
    expect(screen.getByText(/当前阶段:shipped/)).toBeInTheDocument();
    // 阶段标签渲染
    expect(screen.getByText("已联系")).toBeInTheDocument();
    expect(screen.getByText("已寄样")).toBeInTheDocument();
    // 当前阶段徽标
    expect(screen.getByText("当前")).toBeInTheDocument();
    // 以 token + 正确路径请求
    const [path, , token] = apiFetch.mock.calls[0];
    expect(path).toBe("/api/admin/vkpi/projects/42/timeline");
    expect(token).toBe("tok");
  });

  it("错误态:apiFetch reject → 「时间线源异常」+ 仍渲染骨架阶段", async () => {
    apiFetch.mockRejectedValue(new Error("net down"));
    render(<ProjectTimeline apiToken="tok" projectId="7" />);
    expect(await screen.findByText(/时间线源异常/)).toBeInTheDocument();
    expect(screen.getByText(/net down/)).toBeInTheDocument();
    // 降级骨架阶段仍在
    expect(screen.getByText("发现")).toBeInTheDocument();
  });

  it("已签收摘要徽标(shipments.delivered_count)渲染", async () => {
    apiFetch.mockResolvedValue({
      current_stage: "delivered",
      shipments: { delivered_count: 3 },
      stages: [
        { key: "delivered", label_zh: "已签收", index: 0, reached: true, at: null, counts: {} },
      ],
    });
    render(<ProjectTimeline apiToken="tok" projectId="9" />);
    await waitFor(() => expect(screen.getByText(/已签收 3/)).toBeInTheDocument());
  });
});
