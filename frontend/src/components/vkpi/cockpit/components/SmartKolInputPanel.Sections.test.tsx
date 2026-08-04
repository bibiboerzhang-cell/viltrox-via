import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HistoryStrip, PlanPills, RecallMiniItem } from "./SmartKolInputPanel.Sections";

describe("SmartKolInputPanel search quality surfaces", () => {
  it("shows catalog clarification instead of a fabricated search plan", () => {
    render(
      <PlanPills
        plan={{
          status: "needs_clarification",
          clarification: {
            message: "没有在产品目录中找到这个明确型号，请先选择正确产品后再找达人。",
            suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO" }],
          },
        }}
      />,
    );

    expect(screen.getByText(/没有在产品目录中找到/)).toBeTruthy();
    expect(screen.getByText("AF 35mm F1.8 EVO")).toBeTruthy();
    expect(screen.queryByText(/检索词:/)).toBeNull();
  });

  it("shows contact and audience readiness on a candidate card", () => {
    render(
      <RecallMiniItem
        index={1}
        item={{
          bucket: "creator",
          handle: "creator",
          display_name: "Creator",
          platform: "youtube",
          followers: 12000,
          source_fields: {
            contact_preview: { status: "ready", email: "public@example.com" },
            audience_preview: { status: "ready", method: "ensemble_v1", confidence: 0.72 },
          },
        }}
      />,
    );

    expect(screen.getByText("public@example.com")).toBeTruthy();
    expect(screen.getByText("受众估算")).toBeTruthy();
    expect(screen.getByText("受众估算").getAttribute("title")).toContain("ensemble_v1");
  });

  it("shows the business lane, honest backfill, and missing-data state on a candidate card", () => {
    render(
      <RecallMiniItem
        index={1}
        item={{
          kol_pool_id: 88,
          bucket: "creator",
          handle: "expansion_creator",
          display_name: "Expansion Creator",
          platform: "instagram",
          vector_score: 0.64,
          profile_type: "creator",
          type_label: "创作者",
          creator_type_score: 80,
          reviewer_type_score: 20,
          candidate_bucket: "expansion",
          match_tier: "backfill",
          relaxed_filters: ["query_relevance"],
          unknown_fields: ["language", "gear_content"],
        }}
      />,
    );

    expect(screen.getByText("拓展型")).toBeTruthy();
    expect(screen.getByText("补位")).toBeTruthy();
    expect(screen.getByText("补全关键资料 · 2 项")).toBeTruthy();
    expect(screen.queryByText("缺失：内容语言、摄影器材内容")).toBeNull();
    expect(screen.getByText(/为何仅候选/)).toBeTruthy();
  });

  it("keeps history compact and exposes archive, restore, filter, and guarded clear actions", () => {
    const onOpen = vi.fn();
    const onArchive = vi.fn();
    const onRestore = vi.fn();
    const onArchiveAll = vi.fn();
    const active = {
      id: 778,
      query_text: "35mm 低光人像 YouTube 摄影师",
      query_type: "text_recall",
      status: "ready",
      item_count: 12,
      updated_at: "2026-07-12T12:05:00Z",
    };
    const archived = {
      id: 779,
      query_text: "85mm portrait creator",
      query_type: "text_recall",
      status: "partial",
      item_count: 8,
      archived_at: "2026-07-12T13:05:00Z",
      updated_at: "2026-07-12T13:05:00Z",
    };

    render(
      <HistoryStrip
        items={[active]}
        archivedItems={[archived]}
        loading={false}
        onOpen={onOpen}
        onArchive={onArchive}
        onRestore={onRestore}
        onArchiveAll={onArchiveAll}
      />,
    );

    expect(screen.queryByText(active.query_text)).toBeNull();
    fireEvent.click(screen.getByText("历史记录"));
    expect(screen.getByText(active.query_text)).toBeTruthy();
    fireEvent.click(screen.getByLabelText(`移除历史：${active.query_text}`));
    expect(onArchive).toHaveBeenCalledWith(active);

    fireEvent.click(screen.getByText("已移除 1"));
    expect(screen.getByText(archived.query_text)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("筛选历史记录"), { target: { value: "85mm" } });
    fireEvent.click(screen.getByLabelText(`恢复历史：${archived.query_text}`));
    expect(onRestore).toHaveBeenCalledWith(archived);

    fireEvent.click(screen.getByText("最近 1"));
    fireEvent.change(screen.getByLabelText("筛选历史记录"), { target: { value: "" } });
    fireEvent.click(screen.getByText("清理已完成"));
    expect(onArchiveAll).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认清理全部已完成"));
    expect(onArchiveAll).toHaveBeenCalledTimes(1);
  });

  it("keeps the history entry visible for an empty or failed account-scoped history", () => {
    render(
      <HistoryStrip
        items={[]}
        archivedItems={[]}
        loading={false}
        notice="历史记录暂时无法同步，主搜索功能不受影响"
        onOpen={() => undefined}
        onArchive={() => undefined}
        onRestore={() => undefined}
        onArchiveAll={() => undefined}
      />,
    );

    expect(screen.getByText("历史记录")).toBeTruthy();
    expect(screen.getByText("同步异常")).toBeTruthy();
    fireEvent.click(screen.getByText("查看"));
    expect(screen.getByText("当前登录账号暂无搜索历史")).toBeTruthy();
    expect(screen.getByText("历史记录暂时无法同步，主搜索功能不受影响")).toBeTruthy();
  });
});
