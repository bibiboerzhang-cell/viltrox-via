import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsBusinessIntegrationsPanel } from "./SettingsBusinessIntegrationsPanel";
import { getBusinessIntegrationsStatus } from "../../../../services/vkpi/settings-api";

vi.mock("../../../../services/vkpi/settings-api", () => ({
  getBusinessIntegrationsStatus: vi.fn(),
}));

const statusMock = vi.mocked(getBusinessIntegrationsStatus);

describe("SettingsBusinessIntegrationsPanel", () => {
  beforeEach(() => {
    statusMock.mockResolvedValue({
      generated_at: "2026-07-14T16:00:00Z",
      claim_status: "descriptive_only",
      write_performed: false,
      secrets_returned: false,
      integrations: [
        {
          key: "shopify",
          title: "Shopify 订单",
          state: "not_configured",
          summary: "等待 Shopify 授权；当前真实订单与 GMV 均为 0。",
          data_quality: "empty",
          evidence: { orders: 0, successful_sync_runs: 0 },
          source: "vkpi_shopify_orders",
          next_action: "进入 Shopify 授权入口。",
          operator_status: "awaiting_authorization",
          operator_label: "待授权",
        },
        {
          key: "inventory",
          title: "真实库存",
          state: "pending",
          summary: "384 条目录/旧数据中 0 条已核验。",
          data_quality: "unverified",
          evidence: { rows: 384, verified_non_sample: 0 },
          source: "vkpi_inventory",
          next_action: "逐条确认数量来源。",
          operator_status: "awaiting_configuration",
          operator_label: "待配置",
        },
        {
          key: "r2",
          title: "R2 媒体缓存",
          state: "pending",
          summary: "发现历史缓存，但尚无本轮上传回读 canary。",
          data_quality: "partial",
          evidence: { required_env_configured: 4, historical_cached_assets: 23 },
          source: "runtime configuration + vkpi_video_analysis_cache",
          next_action: "授权后执行上传、回读与 SHA 校验。",
          operator_status: "awaiting_configuration",
          operator_label: "待配置",
        },
        {
          key: "outcomes",
          title: "真实业务结果 / 学习回传",
          state: "not_configured",
          summary: "尚无可计入学习成熟度的人工结果。",
          data_quality: "empty",
          evidence: { evidence_backed_finalized_outcomes: 0, verified_actual_evals: 0 },
          source: "vkpi_gtm_outcomes + vkpi_prediction_evals",
          next_action: "在结果复盘中由人工裁决。",
          operator_status: "awaiting_configuration",
          operator_label: "待配置",
        },
      ],
    });
  });

  it("keeps missing and unverified business data visibly non-connected", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    expect(await screen.findByText("Shopify 订单")).toBeTruthy();
    expect(screen.getByText("待授权")).toBeTruthy();
    expect(screen.getAllByText("待配置").length).toBeGreaterThan(0);
    expect(screen.getAllByText("无真实数据").length).toBeGreaterThan(0);
    expect(screen.getByText("数据未核验")).toBeTruthy();
    expect(screen.getByText(/descriptive_only/)).toBeTruthy();
  });

  it("routes Shopify, inventory and outcomes to existing real boards", async () => {
    const onOpenArea = vi.fn();
    render(<SettingsBusinessIntegrationsPanel apiToken="token" onOpenArea={onOpenArea} />);
    await screen.findByText("Shopify 订单");

    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权" }));
    fireEvent.click(screen.getByRole("button", { name: "打开库存管理" }));
    fireEvent.click(screen.getByRole("button", { name: "打开人工结果复盘" }));

    await waitFor(() => expect(onOpenArea).toHaveBeenNthCalledWith(1, "shopify"));
    expect(onOpenArea).toHaveBeenNthCalledWith(2, "events");
    expect(onOpenArea).toHaveBeenNthCalledWith(3, "gtmCommand");
  });

  it("opens a secret-free R2 diagnostic without claiming the pending connection is ready", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("R2 媒体缓存");

    const trigger = screen.getByRole("button", { name: "查看 R2 安全诊断" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);

    expect(screen.getByRole("region", { name: "R2 安全诊断与配置说明" })).toHaveTextContent("不会保存或回显 Access Key");
    expect(screen.getByRole("region", { name: "R2 安全诊断与配置说明" })).toHaveTextContent("上传 → 回读 → SHA 校验 canary");
    expect(screen.getAllByText("待配置").length).toBeGreaterThan(0);
  });
});
