import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// 优化波 B · F4/F5 在智能找人面上的落点:
//   · 发现墙卡片带 👍/👎(有 token + kol_pool_id 才渲染;未入库发现项不渲染)
//   · 「数据源暂不可用」必须带原因;没有 provider_gate_reason 就说「未就绪(配置/预算)」
//   · Facebook 标签 = 「仅候选池」

vi.mock("../../../../services/http", () => ({
  apiFetch: vi.fn().mockResolvedValue({ ok: true, feedback_id: 1 }),
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { RecallMiniItem } from "./SmartKolInputPanel.Sections";
import { providerGateReasonOf, providerUnavailableLabel } from "./SmartKolInputPanel.helpers";
import { onlineQualifiedSummaryFromSession } from "./SmartKolInputPanel.OnlineQualified";
import { discoveryItemsFromSession } from "./SmartKolInputPanel.derivers";

const baseItem = {
  kol_pool_id: 501,
  handle: "creator_a",
  display_name: "Creator A",
  platform: "youtube",
  profile_type: "creator",
  bucket: "creator",
  type_label: "全网发现",
  creator_type_score: 1,
  reviewer_type_score: 0,
  followers: 12000,
} as any;

describe("发现墙卡片 · 👍/👎 最小标注(F4)", () => {
  afterEach(() => cleanup());

  it("传 feedbackSource + token 且有 kol_pool_id 才渲染控件", () => {
    render(<RecallMiniItem item={baseItem} index={1} feedbackSource="discovery_wall" feedbackToken="tok" />);
    const control = screen.getByTestId("search-feedback-control");
    expect(control.getAttribute("data-feedback-source")).toBe("discovery_wall");
    cleanup();
    render(<RecallMiniItem item={{ ...baseItem, kol_pool_id: 0 }} index={1} feedbackSource="discovery_wall" feedbackToken="tok" />);
    expect(screen.queryByTestId("search-feedback-control")).toBeNull();
    cleanup();
    render(<RecallMiniItem item={baseItem} index={1} />);
    expect(screen.queryByTestId("search-feedback-control")).toBeNull();
  });

  it("发现项派生带 session_item_id(会话 item id),供标注回传", () => {
    const session = {
      id: 1,
      items: [
        { id: 777, item_type: "new_creator", kol_pool_id: 501, payload: { handle: "creator_a", platform: "youtube", followers: 100 } },
        { item_type: "new_creator", kol_pool_id: 502, payload: { handle: "creator_b", platform: "tiktok", followers: 100 } },
      ],
    } as any;
    const items = discoveryItemsFromSession(session);
    expect(items[0].session_item_id).toBe(777);
    expect(items[1].session_item_id).toBeUndefined();
  });
});

describe("数据源状态诚实化(F5)", () => {
  it("provider_failed 带 provider_gate_reason;缺席时写「未就绪(配置/预算)」,不假排队", () => {
    expect(providerUnavailableLabel("")).toBe("数据源未就绪(配置/预算)");
    expect(providerUnavailableLabel("budget_exhausted")).toBe("数据源暂不可用(预算已用尽)");
    expect(providerUnavailableLabel("custom_reason")).toBe("数据源暂不可用(custom_reason)");
    expect(providerGateReasonOf({ provider_gate_reason: "not_configured" })).toBe("not_configured");
    expect(providerGateReasonOf({ provider_gate: { reason: "disabled" } })).toBe("disabled");
    expect(providerGateReasonOf({})).toBe("");
  });

  it("联网合同 shortfall_reasons.provider_failed 渲染为带原因的标签", () => {
    const contract = (extra: Record<string, unknown>) => ({
      id: 9,
      result_summary: {
        online_qualification: {
          schema: "smart_online_net_new_qualified_v1",
          policy_version: 1,
          server_owned: true,
          origin_lane: "online",
          source: "platform_discovery_strict",
          target_count: 30,
          net_new_accepted_count: 0,
          returned_count: 0,
          snapshot_revision: 1,
          snapshot_id: "snap-1",
          shortfall: 30,
          shortfall_reasons: { provider_failed: 4 },
          ...extra,
        },
      },
    }) as any;
    const withReason = onlineQualifiedSummaryFromSession(contract({ provider_gate_reason: "budget" }));
    const withoutReason = onlineQualifiedSummaryFromSession(contract({}));
    const joined = (summary: { shortfallReasons: string[]; contractValid: boolean }) => summary.shortfallReasons.join(" | ");
    expect(withReason.contractValid).toBe(true);
    expect(joined(withReason)).toContain("数据源暂不可用(预算闸) 4");
    expect(joined(withoutReason)).toContain("数据源未就绪(配置/预算) 4");
    expect(joined(withoutReason)).not.toMatch(/排队/);
  });
});
