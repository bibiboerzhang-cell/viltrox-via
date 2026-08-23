import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const httpMocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}));

vi.mock("../../../../services/http", () => ({
  apiFetch: httpMocks.apiFetch,
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { resetSearchFeedbackStore } from "../../../../services/vkpi/searchFeedback-api";
import { SearchFeedbackControl } from "./SearchFeedbackControl";

describe("SearchFeedbackControl · 👍/👎 最小标注(F4)", () => {
  beforeEach(() => {
    httpMocks.apiFetch.mockReset().mockResolvedValue({ ok: true, feedback_id: 1 });
    resetSearchFeedbackStore();
  });
  afterEach(() => {
    cleanup();
    resetSearchFeedbackStore();
  });

  it("无 token / 无 kol_pool_id 不渲染(不给假按钮)", () => {
    const { container } = render(<SearchFeedbackControl source="discovery_wall" kolPoolId={5} apiToken="" />);
    expect(container.querySelector("[data-testid=search-feedback-control]")).toBeNull();
    cleanup();
    const second = render(<SearchFeedbackControl source="discovery_wall" kolPoolId={0} apiToken="tok" />);
    expect(second.container.querySelector("[data-testid=search-feedback-control]")).toBeNull();
  });

  it("👍 直接提交并乐观高亮;同判定重复点击不重复打接口", async () => {
    render(<SearchFeedbackControl source="discovery_wall" kolPoolId={5} sessionItemId={77} apiToken="tok" />);
    const up = screen.getByRole("button", { name: "这条推荐合适" });
    await act(async () => {
      fireEvent.click(up);
    });
    expect(up.getAttribute("aria-pressed")).toBe("true");
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(httpMocks.apiFetch.mock.calls[0][1].body)).toEqual({ source: "discovery_wall", kol_pool_id: 5, session_item_id: 77, verdict: "up" });
    await act(async () => {
      fireEvent.click(up);
    });
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("search-feedback-control").getAttribute("data-feedback-status")).toBe("saved");
  });

  it("👎 先弹原因闭集,选原因才提交,reason 进 body", async () => {
    render(<SearchFeedbackControl source="kol_detail" kolPoolId={8} apiToken="tok" />);
    fireEvent.click(screen.getByRole("button", { name: "这条推荐不合适" }));
    expect(httpMocks.apiFetch).not.toHaveBeenCalled();
    const menu = screen.getByTestId("search-feedback-reasons");
    const reasons = Array.from(menu.querySelectorAll("[data-feedback-reason]")).map((node) => node.getAttribute("data-feedback-reason"));
    expect(reasons).toEqual(["not_relevant", "wrong_region", "too_small", "brand_official", "duplicate", "other"]);
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "品牌官方账号" }));
    });
    expect(httpMocks.apiFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(httpMocks.apiFetch.mock.calls[0][1].body)).toEqual({ source: "kol_detail", kol_pool_id: 8, verdict: "down", reason: "brand_official" });
    expect(screen.queryByTestId("search-feedback-reasons")).toBeNull();
    expect(screen.getByRole("button", { name: "这条推荐不合适" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("提交失败显示「未保存」可重试", async () => {
    httpMocks.apiFetch.mockRejectedValueOnce(new Error("网络错误"));
    render(<SearchFeedbackControl source="kol_detail" kolPoolId={8} apiToken="tok" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "这条推荐合适" }));
    });
    expect(screen.getByText("未保存")).toBeTruthy();
    expect(screen.getByTestId("search-feedback-control").getAttribute("data-feedback-status")).toBe("error");
  });
});
