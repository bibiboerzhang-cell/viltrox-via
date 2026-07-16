import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listKolSearchHistory = vi.fn();
vi.mock("../../../../services/vkpi/kolPool-api", () => ({
  listKolSearchHistory: (...args: unknown[]) => listKolSearchHistory(...args),
}));

import {
  KolProfileHistoryModule,
  buildProfileHistoryEvents,
  searchSessionReferencesKol,
} from "./KolProfileHistoryModule";

const ready = (data: Record<string, unknown>) => ({ status: "ready" as const, data, error: "" });

const ACTIVE = {
  id: 7,
  query_text: "85mm creators",
  query_type: "text_recall",
  source: "smart_kol_input",
  status: "ready",
  created_by: 12,
  result_summary: { matched_kol_pool_id: 101 },
  item_count: 4,
  archive_reason: "user_removed",
  archived_at: null,
  created_at: "2026-07-01T10:00:00Z",
  updated_at: "2026-07-09T10:00:00Z",
};

const ARCHIVED = {
  id: 8,
  query_text: "profile URL",
  query_type: "url_profile",
  source: "url_deep_crawl",
  status: "ready",
  created_by: 12,
  items_preview: [{ kol_pool_id: 101 }],
  item_count: 1,
  archive_reason: "user_removed",
  archived_at: "2026-07-08T10:00:00Z",
  archived_by: 12,
  created_at: "2026-06-30T10:00:00Z",
  updated_at: "2026-07-08T10:00:00Z",
};

const DEEP = {
  items: [
    { id: 30, analysis_kind: "profile_llm", status: "ready", provider: "gemini", method: "profile_v1", source_evidence_id: 900, created_at: "2026-07-10T10:00:00Z" },
    { id: 31, analysis_kind: "video_deep", status: "ready", provider: "claude", method: "final_v1", source_cache_id: 44, created_at: "2026-07-07T10:00:00Z" },
  ],
};

const COOP = {
  events: [
    { id: 40, action_label: "评估", status_label: "评估中", actor_staff_id: 22, note: "等待样片", created_at: "2026-07-06T10:00:00Z" },
    { id: 41, action_label: "备注", status_label: "评估中", note: "无操作者字段", created_at: "2026-07-05T10:00:00Z" },
  ],
};

beforeEach(() => {
  listKolSearchHistory.mockReset().mockImplementation(async (_token: string, params: { archived?: boolean }) => ({
    status: "ready",
    items: params.archived ? [ARCHIVED] : [ACTIVE, { id: 99, query_text: "unrelated", result_summary: { matched_kol_pool_id: 202 } }],
  }));
});

describe("KOL 档案统一历史", () => {
  it("仅以精确 KOL ID 关联，不以相似 handle 或自由文本猜测", () => {
    expect(searchSessionReferencesKol({ query_text: "@alpha", result_summary: { matched_kol_pool_id: 202 } }, 101)).toBe(false);
    expect(searchSessionReferencesKol({ items_preview: [{ payload: { nested: { kol_pool_id: 101 } } }] }, 101)).toBe(true);
    expect(searchSessionReferencesKol({ approved_kol_ids: [101, 102] }, 101)).toBe(true);
  });

  it("聚合搜索、移除/恢复、深析和合作事实；恢复时间与操作者限制明确", () => {
    const events = buildProfileHistoryEvents({
      kolId: 101,
      activeSessions: [ACTIVE],
      archivedSessions: [ARCHIVED],
      deepData: DEEP,
      cooperationData: COOP,
    });
    expect(events).toHaveLength(8);
    const restored = events.find((event) => event.title.includes("已恢复"));
    expect(restored?.occurredAt).toBe("2026-07-09T10:00:00Z");
    expect(restored?.operator).toContain("未知");
    expect(restored?.provenance.join(" ")).toContain("后端尚无独立 restored_at");
    expect(events.find((event) => event.title.includes("已移除"))?.operator).toBe("员工 #12");
    expect(events.find((event) => event.title.includes("深析结果"))?.source).toContain("gemini");
  });

  it("页面显示来源、操作者、筛选和分页；读取接口保持只读", async () => {
    render(
      <KolProfileHistoryModule
        apiToken="token"
        kolId={101}
        reloadTick={0}
        deep={ready(DEEP)}
        cooperation={ready(COOP)}
      />,
    );
    expect(await screen.findByText("搜索会话已恢复到历史")).toBeTruthy();
    expect(screen.getAllByText(/操作者：员工 #12/).length).toBeGreaterThan(0);
    expect(screen.getByText("1 / 2")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("下一页历史"));
    expect(screen.getByText("2 / 2")).toBeTruthy();
    fireEvent.click(screen.getByText(/深析 2/));
    expect(screen.getByText("1 / 1")).toBeTruthy();
    expect(screen.getAllByText(/深析结果/, { selector: "b" })).toHaveLength(2);

    await waitFor(() => expect(listKolSearchHistory).toHaveBeenCalledTimes(2));
    for (const [, params] of listKolSearchHistory.mock.calls) {
      expect(params).toMatchObject({ limit: 50, itemLimit: 10 });
    }
  });

  it("部分来源失败时保留其余事实；全空时显示诚实空态", async () => {
    listKolSearchHistory
      .mockRejectedValueOnce(new Error("active unavailable"))
      .mockResolvedValueOnce({ status: "ready", items: [] });
    render(
      <KolProfileHistoryModule
        apiToken="token"
        kolId={101}
        reloadTick={0}
        deep={{ status: "error", data: null, error: "deep unavailable" }}
        cooperation={ready({ events: [] })}
      />,
    );
    expect(await screen.findByText(/部分来源读取失败/)).toBeTruthy();
    expect(screen.getByText("该范围内没有可核验历史")).toBeTruthy();
    expect(screen.getByText(/不以用户名相似/)).toBeTruthy();
  });
});
