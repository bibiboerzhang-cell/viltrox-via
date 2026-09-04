import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import { HistoryStrip, PlanPills, RecallMiniItem } from "./SmartKolInputPanel.Sections";

describe("SmartKolInputPanel search quality surfaces", () => {
  it("shows catalog clarification instead of a fabricated search plan", () => {
    render(
      <PlanPills
        plan={{
          status: "needs_clarification",
          original_query: "找 35 evo 摄影师",
          clarification: {
            message: "没有在产品目录中找到这个明确型号，请先选择正确产品后再找达人。",
            suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO" }],
          },
        }}
        currentQuery="找 35 evo 摄影师"
      />,
    );

    expect(screen.getByText(/没有在产品目录中找到/)).toBeTruthy();
    expect(screen.getByText("AF 35mm F1.8 EVO")).toBeTruthy();
    expect(screen.queryByText(/检索词:/)).toBeNull();
  });

  it("lets keyboard users choose a canonical product and continue without typing a SKU", async () => {
    const onSuggestionSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <PlanPills
        plan={{
          status: "needs_clarification",
          original_query: "找 35 evo 摄影师",
          clarification: {
            message: "请选择目录产品",
            suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO", mount: "FE-mount" }],
          },
        }}
        currentQuery="找 35 evo 摄影师"
        onSuggestionSelect={onSuggestionSelect}
      />,
    );

    const choice = screen.getByRole("button", { name: "选择产品 AF 35mm F1.8 EVO 并自动继续搜索" });
    expect(choice).toHaveAttribute("data-product-sku", "AF-35-EVO");
    await user.tab();
    expect(choice).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onSuggestionSelect).toHaveBeenCalledWith(
      expect.objectContaining({ sku: "AF-35-EVO" }),
      "找 35 evo 摄影师",
    );
  });

  it("locks a clarification choice when the input no longer matches the plan query", async () => {
    const onSuggestionSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <PlanPills
        plan={{
          status: "needs_clarification",
          original_query: "find wedding photographers",
          clarification: {
            message: "请选择目录产品",
            suggestions: [{ sku: "AF-35-EVO", name: "AF 35mm F1.8 EVO" }],
          },
        }}
        currentQuery="find basketball storytellers"
        resultsStale
        onSuggestionSelect={onSuggestionSelect}
      />,
    );

    const choice = screen.getByRole("button", { name: "选择产品 AF 35mm F1.8 EVO 并自动继续搜索" });
    expect(choice).toBeDisabled();
    await user.click(choice);
    expect(onSuggestionSelect).not.toHaveBeenCalled();
  });

  it("shows the business objective and independent first-round scene queries without provider details", () => {
    render(
      <PlanPills
        plan={{
          objective: "prospective_growth",
          search_brief: {
            objective: "prospective_growth",
            query_cells: [
              {
                query_cell_id: "segment_1_motorsport",
                segment_label: "赛车拍摄",
                primary_query: "motorsport camera creator fast autofocus",
                segment_source: "provider_internal",
              },
              {
                query_cell_id: "segment_2_food",
                segment_label: "厨师餐饮",
                primary_query: "chef restaurant video creator low light",
                segment_source: "apify",
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByTestId("search-objective-summary")).toHaveTextContent("寻找会用产品并能推动市场的创作者");
    expect(screen.getByTestId("query-cell-summary")).toHaveTextContent("场景 1赛车拍摄· 首轮查询：motorsport camera creator fast autofocus");
    expect(screen.getByTestId("query-cell-summary")).toHaveTextContent("场景 2厨师餐饮· 首轮查询：chef restaurant video creator low light");
    expect(screen.queryByText(/provider_internal|apify/i)).toBeNull();
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

  it("uses the progress contract to close empty and stale raw history states honestly", () => {
    const emptyPartial: VkpiKolSearchHistoryItem = {
      id: 1142,
      query_text: "empty terminal search",
      query_type: "text_recall",
      status: "partial",
      item_count: 0,
      progress_contract: {
        schema: "kol_search_progress_v1",
        state: "partial",
        requested_units: 0,
        successful_units: 0,
        terminal_units: 0,
        requested_tasks_terminal: true,
        requested_tasks_successful: false,
        completion_kind: "empty_result",
        empty_result: true,
        stages: {},
        worker: { observed: true, state: "online", online: true },
      },
    };
    const staleRawPartial: VkpiKolSearchHistoryItem = {
      id: 1144,
      query_text: "requested stages done",
      query_type: "text_recall",
      status: "running",
      effective_status: "ready",
      item_count: 30,
      progress_contract: {
        schema: "kol_search_progress_v1",
        state: "ready",
        requested_units: 56,
        successful_units: 56,
        terminal_units: 56,
        requested_tasks_terminal: true,
        requested_tasks_successful: true,
        completion_kind: "requested_stages",
        empty_result: false,
        stages: {},
        worker: { observed: true, state: "online", online: true },
      },
    };

    render(
      <HistoryStrip
        items={[emptyPartial, staleRawPartial]}
        archivedItems={[]}
        loading={false}
        onOpen={() => undefined}
        onArchive={() => undefined}
        onRestore={() => undefined}
        onArchiveAll={() => undefined}
      />,
    );

    fireEvent.click(screen.getByText("历史记录"));
    expect(screen.getByText("无结果，已结束")).toBeTruthy();
    expect(screen.getByText("已请求阶段完成")).toBeTruthy();
    expect(screen.getByLabelText("移除历史：requested stages done")).toBeDisabled();
    expect(screen.getByRole("button", { name: "清理已完成" })).toBeTruthy();
  });

  it("keeps raw-running effective-terminal history read-only until the durable row closes", () => {
    render(
      <HistoryStrip
        items={[{
          id: 1144,
          query_text: "requested stages done",
          query_type: "text_recall",
          status: "running",
          effective_status: "ready",
          progress_contract: {
            schema: "kol_search_progress_v1",
            state: "ready",
            requested_units: 56,
            successful_units: 56,
            terminal_units: 56,
            requested_tasks_terminal: true,
            stages: {},
            worker: { observed: true, state: "online", online: true },
          },
        }]}
        archivedItems={[]}
        loading={false}
        onOpen={() => undefined}
        onArchive={() => undefined}
        onRestore={() => undefined}
        onArchiveAll={() => undefined}
      />,
    );

    fireEvent.click(screen.getByText("历史记录"));
    expect(screen.getByText("已请求阶段完成")).toBeTruthy();
    expect(screen.getByLabelText("移除历史：requested stages done")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "清理已完成" })).toBeNull();
  });

  it("treats both cancellation spellings as terminal history instead of queued", () => {
    render(
      <HistoryStrip
        items={[
          { id: 1145, query_text: "cancelled search", query_type: "text_recall", status: "cancelled" },
          { id: 1146, query_text: "canceled search", query_type: "text_recall", status: "canceled" },
        ]}
        archivedItems={[]}
        loading={false}
        onOpen={() => undefined}
        onArchive={() => undefined}
        onRestore={() => undefined}
        onArchiveAll={() => undefined}
      />,
    );

    fireEvent.click(screen.getByText("历史记录"));
    expect(screen.getAllByText("已取消")).toHaveLength(2);
    expect(screen.getByLabelText("移除历史：cancelled search")).toBeEnabled();
    expect(screen.getByLabelText("移除历史：canceled search")).toBeEnabled();
  });
});
