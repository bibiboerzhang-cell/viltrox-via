// U10:抽屉「联系方式」行只出两档揭示徽标(已核验 / 观测到 · 未核验),
// 不再把 contact.source / contact.verificationStatus 这类内部码(raw_bio_scan / observed …)露到门面。
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KOLDrawerContactAndVideos } from "./KOLDetailDrawerSections";
import type { ContactState } from "../lib/kolContacts";

const item = { id: 7, display_name: "Future Shock Studios", platform: "youtube", profile_url: "https://www.youtube.com/@fss" };

function renderContacts(contactState: ContactState) {
  return render(
    <KOLDrawerContactAndVideos
      item={item}
      representativeVideos={[]}
      onOpenVideo={vi.fn()}
      contactState={contactState}
      onRetryContact={vi.fn()}
    />,
  );
}

describe("KOLDrawerContactAndVideos disclosure tiers", () => {
  it("renders the verified badge with the verification date and hides raw source codes", () => {
    renderContacts({
      status: "full",
      contacts: [{
        type: "email", label: "邮箱", value: "hello@fss.tv", action: "email", actionLabel: "发邮件", masked: false,
        href: "mailto:hello@fss.tv", source: "public_business_page", tier: "verified",
        verificationStatus: "verified", lastVerifiedAt: "2026-08-20",
      }],
    });

    const badge = screen.getByText("已核验");
    expect(badge).toHaveAttribute("data-contact-tier", "verified");
    expect(screen.getByText("核验 2026-08-20")).toBeTruthy();
    expect(document.body.textContent).not.toContain("public_business_page");
    expect(document.body.textContent).not.toContain("verified_status");
  });

  it("renders the observed badge without a verification date and without internal words", () => {
    renderContacts({
      status: "full",
      contacts: [{
        type: "email", label: "邮箱", value: "scan@fss.tv", action: "email", actionLabel: "发邮件", masked: false,
        source: "raw_bio_scan", tier: "observed", verificationStatus: "observed", lastVerifiedAt: "",
      }],
    });

    const badge = screen.getByText("观测到 · 未核验");
    expect(badge).toHaveAttribute("data-contact-tier", "observed");
    expect(screen.queryByText(/^核验 /)).toBeNull();
    expect(document.body.textContent).not.toContain("raw_bio_scan");
    // 内部态词 observed 只允许出现在 data-contact-tier 属性,不得成为可见文本
    expect(document.body.textContent).not.toMatch(/\bobserved\b/);
  });

  it("shows no tier badge for legacy contacts that carry neither tier nor status", () => {
    renderContacts({
      status: "full",
      contacts: [{
        type: "website", label: "网站", value: "https://fss.tv", action: "website", actionLabel: "打开", masked: false,
        href: "https://fss.tv", source: "legacy_import",
      }],
    });

    expect(document.querySelector("[data-contact-tier]")).toBeNull();
    expect(document.body.textContent).not.toContain("legacy_import");
  });
});
