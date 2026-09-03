import React from "react";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LOCALE_STORAGE_KEY, LocaleProvider } from "../../app/providers/LocaleProvider";

vi.mock("../../shared/ThemeSwitch", () => ({ ThemeSwitch: () => null }));
vi.mock("../../lib/buildInfo", () => ({
  frontendBuildInfo: { gitBranch: "test", gitSha: "abcdef123456", builtAt: "2026-09-02T00:00:00Z" },
  shortBuildSha: (value: string) => value.slice(0, 8),
}));

const { fetchLegalPolicy } = vi.hoisted(() => ({ fetchLegalPolicy: vi.fn() }));
vi.mock("./legalApi", async () => {
  const actual = await vi.importActual<typeof import("./legalApi")>("./legalApi");
  return { ...actual, fetchLegalPolicy, submitDsarRequest: vi.fn() };
});

import LegalRoute from "./LegalRoute";

const POLICY = {
  status: "draft",
  draft: true,
  legal_review: "pending",
  version: "2026-09-02-draft",
  contact_email: "privacy@viltrox.com",
  contact_email_configured: false,
  retention: [
    { bucket: "apify_payload", policy_key: "VKPI_RETENTION_APIFY_PAYLOAD_DAYS", days: 45, default_days: 90, label_zh: "原始抓取载荷", label_en: "Raw payloads" },
    { bucket: "comments", policy_key: "VKPI_RETENTION_COMMENTS_DAYS", days: 180, default_days: 180, label_zh: "公开评论原文", label_en: "Public comment text" },
    { bucket: "suppressed_contacts", policy_key: "contact_suppression", days: 0, default_days: 0, label_zh: "已抑制联系方式", label_en: "Suppressed contacts" },
  ],
  purge_task_key: "vkpi_data_retention_purge",
  purge_gate_env: "VKPI_DATA_RETENTION_PURGE",
  purge_enabled: false,
  dsar_sla_days: 30,
  public_form_path: "/legal/request",
  request_types: ["erasure", "access", "do_not_contact"],
  platforms: ["youtube", "instagram", "tiktok", "bilibili", "other"],
};

function renderAt(path: string) {
  return render(
    <LocaleProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/legal" element={<LegalRoute />} />
          <Route path="/legal/:page" element={<LegalRoute />} />
        </Routes>
      </MemoryRouter>
    </LocaleProvider>,
  );
}

describe("LegalRoute", () => {
  beforeEach(() => {
    window.localStorage.clear();
    fetchLegalPolicy.mockReset();
    fetchLegalPolicy.mockResolvedValue(POLICY);
  });

  it("隐私页:草案标记 + W6 策略键 + 接口现值 + 删除/勿联系通道 + 占位邮箱", async () => {
    renderAt("/legal/privacy");

    expect(screen.getByRole("note")).toHaveTextContent("草案 · 待法务审阅");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("隐私政策(草案)");
    expect(screen.getByRole("main")).toHaveTextContent("公开平台抓取");
    expect(screen.getByRole("main")).toHaveTextContent("勿联系");

    const table = await screen.findByRole("table");
    expect(within(table).getByText("VKPI_RETENTION_APIFY_PAYLOAD_DAYS")).toBeInTheDocument();
    expect(within(table).getByText("VKPI_RETENTION_COMMENTS_DAYS")).toBeInTheDocument();
    expect(within(table).getByText("45")).toBeInTheDocument(); // 接口现值,而非默认 90
    expect(within(table).getByText("即时")).toBeInTheDocument();
    expect(screen.getByText("vkpi_data_retention_purge")).toBeInTheDocument();
    expect(screen.getByText("VKPI_DATA_RETENTION_PURGE")).toBeInTheDocument();
    expect(await screen.findByText("数值为当前生效值", { exact: false })).toBeInTheDocument();

    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "privacy@viltrox.com" })).toHaveAttribute("href", "mailto:privacy@viltrox.com");
    expect(footer).toHaveTextContent("占位,待确认");
    expect(within(footer).getByRole("link", { name: "返回登录" })).toHaveAttribute("href", "/login");

    const nav = screen.getByRole("navigation", { name: "法务页导航" });
    expect(within(nav).getAllByRole("link")).toHaveLength(4);
    expect(within(nav).getByRole("link", { name: "隐私政策" })).toHaveAttribute("aria-current", "page");
  });

  it("接口不可用时回落默认值并如实标注", async () => {
    fetchLegalPolicy.mockRejectedValue(new Error("offline"));
    renderAt("/legal/privacy");

    expect(await screen.findByText("接口暂不可用,显示默认值", { exact: false })).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("VKPI_PORTAL_TOKEN_TTL_DAYS")).toBeInTheDocument();
    expect(within(table).getAllByText("90")).toHaveLength(2);
    expect(within(table).getByText("180")).toBeInTheDocument();
  });

  it("目录页与未知页都显示四个入口;条款与数据来源页各自成文", () => {
    const { unmount } = renderAt("/legal");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("法务与隐私");
    // 导航栏 + 目录卡各一个入口,都指向同一页
    const sourceLinks = screen.getAllByRole("link", { name: /数据来源声明/ });
    expect(sourceLinks).toHaveLength(2);
    sourceLinks.forEach((link) => expect(link).toHaveAttribute("href", "/legal/data-sources"));
    unmount();

    const unknown = renderAt("/legal/nope");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("法务与隐私");
    unknown.unmount();

    const terms = renderAt("/legal/terms");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("服务条款(草案)");
    expect(screen.getByRole("main")).toHaveTextContent("待法务确认");
    terms.unmount();

    renderAt("/legal/data-sources");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("数据来源声明(草案)");
    expect(screen.getByRole("main")).toHaveTextContent("只采集无需登录即可公开查看的内容");
  });

  it("申请页挂表单;英文模式整页切英文", async () => {
    renderAt("/legal/request");
    expect(screen.getByRole("form", { name: "申请表" })).toBeInTheDocument();
    expect(screen.getByLabelText("申请类型")).toBeInTheDocument();
  });

  it("英文模式下文案整段切换(不走词表机翻)", async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    renderAt("/legal/terms");
    expect(await screen.findByRole("heading", { level: 1, name: "Terms of Service (draft)" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Draft — pending legal review");
    expect(screen.getByRole("navigation", { name: "Legal pages" })).toBeInTheDocument();
  });
});
