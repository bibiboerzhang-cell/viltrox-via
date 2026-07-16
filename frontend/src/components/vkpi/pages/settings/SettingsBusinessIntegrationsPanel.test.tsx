import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsBusinessIntegrationsPanel } from "./SettingsBusinessIntegrationsPanel";
import {
  connectShopifyClientCredentials,
  getBusinessIntegrationsStatus,
  probeShopifyConnection,
  registerShopifyWebhooks,
  saveShopifyConnectionCredentials,
} from "../../../../services/vkpi/settings-api";

vi.mock("../../../../services/vkpi/settings-api", () => ({
  connectShopifyClientCredentials: vi.fn(),
  getBusinessIntegrationsStatus: vi.fn(),
  probeShopifyConnection: vi.fn(),
  registerShopifyWebhooks: vi.fn(),
  saveShopifyConnectionCredentials: vi.fn(),
}));

const statusMock = vi.mocked(getBusinessIntegrationsStatus);
const connectClientCredentialsMock = vi.mocked(connectShopifyClientCredentials);
const saveCredentialsMock = vi.mocked(saveShopifyConnectionCredentials);
const probeConnectionMock = vi.mocked(probeShopifyConnection);
const registerWebhooksMock = vi.mocked(registerShopifyWebhooks);

function fillShopifyFormalForm() {
  fireEvent.change(screen.getByLabelText("店铺域名"), { target: { value: "Demo.MyShopify.com" } });
  fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "client_id_12345" } });
  fireEvent.change(screen.getByLabelText("Client Secret"), { target: { value: "client_secret_1234567890" } });
}

function fillShopifyLegacyForm() {
  fireEvent.click(screen.getByText("高级：旧 Access Token 兼容模式"));
  fireEvent.change(screen.getByLabelText("Admin API Access Token"), { target: { value: "shpat_test_secret" } });
  fireEvent.change(screen.getByLabelText("Webhook Signing Secret"), { target: { value: "webhook_test_secret" } });
}

describe("SettingsBusinessIntegrationsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    connectClientCredentialsMock.mockResolvedValue({
      ok: true,
      status: "connected",
      phases: {
        authorization: { status: "success" },
        probe: { status: "success" },
        webhooks: { status: "success", registered_count: 3, required_count: 3 },
        commit: { status: "success" },
      },
    });
    saveCredentialsMock.mockResolvedValue({ ok: true, status: "pending" });
    probeConnectionMock.mockResolvedValue({ ok: true, status: "connected" });
    registerWebhooksMock.mockResolvedValue({ ok: true, registered: [] });
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

  it("opens the inline Shopify authorization wizard and keeps real-board navigation", async () => {
    const onOpenArea = vi.fn();
    render(<SettingsBusinessIntegrationsPanel apiToken="token" onOpenArea={onOpenArea} />);
    await screen.findByText("Shopify 订单");

    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    expect(screen.getByRole("region", { name: "Shopify 授权接入向导" })).toHaveTextContent("Client Credentials");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 数据页" }));
    fireEvent.click(screen.getByRole("button", { name: "打开库存管理" }));
    fireEvent.click(screen.getByRole("button", { name: "打开人工结果复盘" }));

    await waitFor(() => expect(onOpenArea).toHaveBeenNthCalledWith(1, "shopify"));
    expect(onOpenArea).toHaveBeenNthCalledWith(2, "events");
    expect(onOpenArea).toHaveBeenNthCalledWith(3, "gtmCommand");
  });

  it("uses one backend atomic endpoint and renders its four-stage receipt without retaining secrets", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fillShopifyFormalForm();

    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    await waitFor(() => expect(connectClientCredentialsMock).toHaveBeenCalled());
    expect(connectClientCredentialsMock).toHaveBeenCalledWith("token", {
      shop_domain: "demo.myshopify.com",
      client_id: "client_id_12345",
      client_secret: "client_secret_1234567890",
    });
    expect(probeConnectionMock).not.toHaveBeenCalled();
    expect(registerWebhooksMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Client Secret")).toHaveValue("");
    expect(screen.getByText(/四阶段技术接入已原子完成/)).toHaveTextContent("不会仅凭配置标记业务就绪");
    expect(document.body.textContent).not.toContain("client_secret_1234567890");
  });

  it("does not roll back the displayed success truth when only the status-card refresh fails", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    statusMock.mockRejectedValueOnce(new Error("status refresh unavailable"));
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fillShopifyFormalForm();
    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    expect(await screen.findByText(/四阶段技术接入已原子完成/)).toBeTruthy();
    expect(await screen.findByText(/新配置已原子启用，但状态卡刷新失败/)).toBeTruthy();
    expect(screen.queryByText(/旧配置保持不变/)).toBeNull();
  });

  it("keeps the legacy access-token path folded as an advanced compatibility mode", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));

    expect(screen.getByLabelText("Admin API Access Token")).not.toBeVisible();
    fillShopifyFormalForm();
    fillShopifyLegacyForm();
    fireEvent.click(screen.getByRole("button", { name: "使用旧 Token 验证" }));

    await waitFor(() => expect(saveCredentialsMock).toHaveBeenCalledWith("token", {
      shop_domain: "demo.myshopify.com",
      access_token: "shpat_test_secret",
      webhook_secret: "webhook_test_secret",
    }));
    expect(connectClientCredentialsMock).not.toHaveBeenCalled();
  });

  it("stops when the token exchange fails and never claims readiness", async () => {
    connectClientCredentialsMock.mockResolvedValue({
      ok: false,
      status: "revoked",
      reason: "provider_rejected_credentials",
      phases: {
        authorization: { status: "error", reason: "provider_rejected_credentials" },
      },
    });
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fillShopifyFormalForm();
    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    expect((await screen.findAllByText(/Shopify 拒绝了当前凭据；旧配置保持不变/)).length).toBeGreaterThan(0);
    expect(probeConnectionMock).not.toHaveBeenCalled();
    expect(registerWebhooksMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/四阶段技术接入已原子完成/)).toBeNull();
    expect(screen.getByLabelText("Client Secret")).toHaveValue("");
    expect(document.body.textContent).not.toContain("client_secret_1234567890");
  });

  it("rejects a noncanonical Shopify domain before any credential request", async () => {
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fireEvent.change(screen.getByLabelText("店铺域名"), { target: { value: "https://demo.myshopify.com/admin" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "client_id_12345" } });
    fireEvent.change(screen.getByLabelText("Client Secret"), { target: { value: "client_secret_1234567890" } });
    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    expect(await screen.findByText(/店铺域名必须是纯 hostname/)).toHaveTextContent("不要填写协议、路径、端口或账号信息");
    expect(saveCredentialsMock).not.toHaveBeenCalled();
    expect(connectClientCredentialsMock).not.toHaveBeenCalled();
    expect(probeConnectionMock).not.toHaveBeenCalled();
    expect(registerWebhooksMock).not.toHaveBeenCalled();
  });

  it("stops after a failed provider probe and never registers webhooks", async () => {
    connectClientCredentialsMock.mockResolvedValue({
      ok: false,
      status: "revoked",
      reason: "provider_rejected_credentials",
      phases: {
        authorization: { status: "success" },
        probe: { status: "error", reason: "provider_rejected_credentials" },
      },
    });
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fillShopifyFormalForm();
    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    const probeFailures = await screen.findAllByText(/Shopify 拒绝了当前凭据/);
    expect(probeFailures.some((row) => row.textContent?.includes("旧配置保持不变"))).toBe(true);
    expect(registerWebhooksMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/四阶段技术接入已原子完成/)).toBeNull();
  });

  it("does not claim readiness when webhook registration fails", async () => {
    connectClientCredentialsMock.mockResolvedValue({
      ok: false,
      reason: "public_base_url_missing",
      phases: {
        authorization: { status: "success" },
        probe: { status: "success" },
        webhooks: { status: "error", reason: "public_base_url_missing" },
      },
    });
    render(<SettingsBusinessIntegrationsPanel apiToken="token" />);
    await screen.findByText("Shopify 订单");
    fireEvent.click(screen.getByRole("button", { name: "打开 Shopify 授权接入" }));
    fillShopifyFormalForm();
    fireEvent.click(screen.getByRole("button", { name: "一步验证并原子启用" }));

    const webhookFailures = await screen.findAllByText(/生产 Webhook 公网地址尚未配置/);
    expect(webhookFailures.some((row) => row.textContent?.includes("旧配置保持不变"))).toBe(true);
    expect(registerWebhooksMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/四阶段技术接入已原子完成/)).toBeNull();
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
