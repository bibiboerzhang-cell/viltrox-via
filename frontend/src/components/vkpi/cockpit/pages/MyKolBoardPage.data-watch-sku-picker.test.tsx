import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DataWatchSkuPicker, type PendingDataWatchSkuChoice } from "./MyKolBoardPage.data-watch-sku-picker";

const pending: PendingDataWatchSkuChoice = {
  video: {
    evidence_id: 91,
    kol_pool_id: 7,
    title: "A tracked video",
    content_url: "https://www.youtube.com/watch?v=abcDEF12345",
  },
  candidates: [
    { sku_code: "AF-85", sku_name: "AF 85mm" },
    { sku_code: "AF-35", sku_name: "AF 35mm", match_source: "final_v1_lens_evidence_v2", modalities: ["visual", "voice"] },
    { sku_code: "AF-85", sku_name: "duplicate must fold" },
  ],
};

describe("内容墙数据关注 SKU 选择器", () => {
  it("不默认伪选 SKU，只提交员工明确勾选的候选", () => {
    const onSubmit = vi.fn();
    render(<DataWatchSkuPicker pending={pending} busy={false} onCancel={vi.fn()} onSubmit={onSubmit} />);

    const submit = screen.getByRole("button", { name: "确认关联并关注" });
    expect(submit).toBeDisabled();
    const checks = screen.getAllByRole("checkbox");
    expect(checks).toHaveLength(2);
    expect(screen.getByText("深析候选：画面/口播")).toBeTruthy();
    fireEvent.click(checks[1]);
    fireEvent.click(screen.getByRole("button", { name: "确认关联并关注（1）" }));
    expect(onSubmit).toHaveBeenCalledWith(["AF-35"], "manual");
  });

  it("空候选诚实提示可查找/精确输入，未选未填时不可提交", () => {
    render(<DataWatchSkuPicker pending={{ ...pending, candidates: [] }} busy={false} onCancel={vi.fn()} onSubmit={vi.fn()} />);
    expect(screen.getByText(/自动候选为空/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认关联并关注" })).toBeDisabled();
  });

  it("候选仅是提示：允许搜索/精确手填目录 SKU，最终仍由服务端校验", () => {
    const onSubmit = vi.fn();
    render(<DataWatchSkuPicker pending={{ ...pending, candidates: [] }} busy={false} onCancel={vi.fn()} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("搜索或输入产品 SKU"), { target: { value: "AF-135-LAB" } });
    fireEvent.click(screen.getByRole("button", { name: "确认关联并关注（1）" }));
    expect(onSubmit).toHaveBeenCalledWith(["AF-135-LAB"], "manual");
  });

  it("唯一 detected 候选仅在员工勾选原项时提交 confirmed 意图", () => {
    const onSubmit = vi.fn();
    const detectedPending: PendingDataWatchSkuChoice = {
      ...pending,
      intent: "confirm_detected",
      candidates: [{
        sku_code: "AF-35",
        sku_name: "AF 35mm",
        match_source: "final_v1_lens_evidence_v2",
        modalities: ["visual", "voice"],
      }],
    };
    render(<DataWatchSkuPicker pending={detectedPending} busy={false} onCancel={vi.fn()} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "确认系统识别并关注（1）" }));
    expect(onSubmit).toHaveBeenCalledWith(["AF-35"], "confirm_detected");
  });

  it("detected 确认面板中手填 SKU 仍保留 manual provenance", () => {
    const onSubmit = vi.fn();
    render(<DataWatchSkuPicker pending={{ ...pending, intent: "confirm_detected", candidates: [pending.candidates[1]] }} busy={false} onCancel={vi.fn()} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText("搜索或输入产品 SKU"), { target: { value: "AF-135-LAB" } });
    fireEvent.click(screen.getByRole("button", { name: "确认关联并关注（1）" }));
    expect(onSubmit).toHaveBeenCalledWith(["AF-135-LAB"], "manual");
  });
});
