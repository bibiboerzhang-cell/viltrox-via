import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LocalQualifiedList, StrictQualifiedList } from "./SmartKolInputPanel.LocalQualifiedList";
import { localQualifiedSummary } from "./SmartKolInputPanel.LocalQualified";
import { readPersistedSearchDisplay, sanitizeSearchDisplayForCache } from "./SmartKolInputPanel.derivers";

function result(items: any[], diagnostics: Record<string, unknown> = {}): any {
  return {
    method: "vector_recall",
    query: {},
    ratio: { creator_quota: 30, reviewer_quota: 0, policy: "soft", mixed_policy: "dominant", dedupe: true },
    items,
    buckets: { creator: items, reviewer: [] },
    diagnostics,
  };
}

function strictProof(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: "smart_local_gate_evidence_v2",
    passed: true,
    account_quality: { passed: true }, followers: { passed: true }, activity: { passed: true },
    market: { passed: true }, language: { passed: true }, profile_type: { passed: true },
    platform: { passed: true }, relevance: { passed: true },
    ...overrides,
  };
}

describe("local qualified first-list contract", () => {
  it("counts only explicit server-qualified unique identities and preserves server rank", () => {
    const summary = localQualifiedSummary(result([
      {
        kol_pool_id: 2,
        handle: "second",
        platform: "youtube",
        followers: 9800,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 2, qualification_status: "qualified" },
      },
      {
        kol_pool_id: 1,
        handle: "first",
        platform: "youtube",
        followers: 12000,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 1, qualification: { status: "accepted" } },
      },
      {
        kol_pool_id: 99,
        handle: "FIRST",
        platform: "youtube",
        followers: 12000,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 4, qualification_status: "qualified" },
      },
      {
        kol_pool_id: 3,
        handle: "promising",
        platform: "instagram",
        followers: 300000,
        source_fields: {
          server_rank: 3,
          latest_video_published_at: "2026-08-10T00:00:00Z",
          market_evidence: { status: "pass", market: "US" },
        },
      },
    ]));

    expect(summary.rows.map((row) => row.name)).toEqual(["first", "second", "promising"]);
    expect(summary.qualified).toBe(2);
    expect(summary.uniqueQualified).toBe(2);
    expect(summary.pending).toBe(1);
    expect(summary.shortfall).toBe(28);
    expect(summary.shortfallReasons[0]).toContain("待服务端硬闸验收");
  });

  it("clamps a strict-v2 aggregate to visible qualified rows and keeps shortfall reasons", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 1, handle: "pending", platform: "youtube", followers: 5000 },
    ], {
      local_lane: {
        schema: "smart_local_qualified_v2",
        target_count: 30,
        qualified_count: 18,
        returned_count: 18,
        unique_qualified_count: 18,
        shortfall_reasons: { freshness_unknown: 7, market_unverified: 5 },
      },
    }));

    expect(summary.qualified).toBe(0);
    expect(summary.serverReturned).toBe(1);
    expect(summary.uniqueQualified).toBe(0);
    expect(summary.pending).toBe(1);
    expect(summary.shortfallReasons).toEqual(["最新视频日期待核验 7", "市场证据待核验 5"]);
  });

  it("does not promote a legacy returned_count without the explicit Smart-local schema", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 1, handle: "legacy-one", platform: "youtube", followers: 5000 },
      { kol_pool_id: 2, handle: "legacy-two", platform: "youtube", followers: 8000 },
    ], { returned_count: 10 }));

    expect(summary.serverReturned).toBe(10);
    expect(summary.qualified).toBe(0);
    expect(summary.serverQualified).toBe(0);
    expect(summary.pending).toBe(2);
    expect(summary.shortfall).toBe(30);
  });

  it("keeps a v1 proof in legacy pending state instead of treating it as strict v2", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 7,
      handle: "legacy-v1",
      platform: "youtube",
      qualification_evidence: { schema: "smart_local_gate_evidence_v1", passed: true },
    }], {
      local_lane: { schema: "smart_local_qualified_v1", qualified_count: 30, returned_count: 30 },
    }));

    expect(summary.qualified).toBe(0);
    expect(summary.rows[0].qualification).toBe("pending");
    expect(summary.serverReturned).toBe(1);
  });

  it("selects only strict server-qualified recall rows and supports select all", () => {
    const onSelectionChange = vi.fn();
    render(<LocalQualifiedList
      result={result([
        { kol_pool_id: 1, handle: "qualified-one", platform: "youtube", qualification_evidence: strictProof() },
        { kol_pool_id: 2, handle: "qualified-two", platform: "instagram", qualification_evidence: strictProof() },
        { kol_pool_id: 3, handle: "legacy-pending", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v1", passed: true } },
        { kol_pool_id: 4, handle: "rejected", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v2", passed: false } },
      ])}
      selectedIds={new Set()}
      onSelectionChange={onSelectionChange}
    />);

    expect((screen.getByRole("checkbox", { name: "选择本地 KOL legacy-pending" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "选择本地 KOL rejected" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "全选本地合格 KOL" }));
    expect([...onSelectionChange.mock.calls[0][0]]).toEqual([1, 2]);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择本地 KOL qualified-one" }));
    expect([...onSelectionChange.mock.calls[1][0]]).toEqual([1]);
  });

  it("keeps an accepted online row unselectable until its server snapshot is terminal", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 31, handle: "online-one", platform: "youtube", qualification_evidence: strictProof() },
    ]));
    render(<StrictQualifiedList
      summary={summary}
      lane="online"
      selectionReady={false}
      selectedIds={new Set()}
      onSelectionChange={vi.fn()}
    />);

    expect(screen.getByText("联网净新增 1/30")).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "选择联网 KOL online-one" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "全选联网净新增 KOL" }) as HTMLInputElement).disabled).toBe(true);
  });

  it("does not qualify an incomplete or internally failed v2 proof", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 20, handle: "top-only", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v2", passed: true } },
      { kol_pool_id: 21, handle: "failed-market", platform: "youtube", qualification_evidence: strictProof({ market: { passed: false } }) },
    ]));
    expect(summary.rows.map((row) => row.qualification)).toEqual(["pending", "rejected"]);
    expect(summary.qualified).toBe(0);
  });

  it("prefers an authoritative polled contact preview over the static facet", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 9,
      handle: "contact-updated",
      platform: "youtube",
      qualification_evidence: strictProof(),
      candidate_facets: { contact_available: "no" },
      source_fields: { contact_preview: { status: "ready" } },
    }, {
      kol_pool_id: 10,
      handle: "contact-empty",
      platform: "youtube",
      qualification_evidence: strictProof(),
      candidate_facets: { contact_available: "yes" },
      source_fields: { contactability: { status: "empty" } },
    }]));

    expect(summary.rows[0].contactStatus).toBe("可联系");
    expect(summary.rows[1].contactStatus).toBe("暂缺");
  });

  it("shows the operational columns but never renders a contact value", () => {
    render(<LocalQualifiedList result={result([{
      kol_pool_id: 8,
      handle: "creator-eight",
      display_name: "Creator Eight",
      platform: "youtube",
      followers: 18800,
      why_fit: "面向美国市场的摄影器材测评",
      qualification_evidence: strictProof({
        followers: { value: 18800, minimum: 3000, known: true, passed: true },
        activity: { posted_at: "2026-08-03T00:00:00Z", passed: true },
        market: { value: "US", passed: true, source: "channel_profile" },
        language: { values: ["en"], targets: ["en"], passed: true },
        profile_type: { values: ["reviewer"], targets: ["reviewer"], passed: true },
        account_quality: { verdict: "eligible_creator_account", passed: true },
      }),
      candidate_facets: { contact_available: "yes" },
      source_fields: {
        server_rank: 1,
        analysis_status: "processing",
      },
    }])} />);

    expect(screen.getByText("本地合格 1/30")).toBeTruthy();
    expect(screen.getByText("Creator Eight")).toBeTruthy();
    expect(screen.getByText(/1\.9万|18\.8K|1\.88万/)).toBeTruthy();
    expect(screen.getByText(/2026.*08.*03/)).toBeTruthy();
    expect(screen.getByText("en")).toBeTruthy();
    expect(screen.getByText("评测号")).toBeTruthy();
    expect(screen.getByText("可联系")).toBeTruthy();
    expect(screen.getByText("分析中")).toBeTruthy();
    expect(screen.queryByText("private@example.com")).toBeNull();
  });

  it("falls back to the server-owned follower proof when the session row has no root value", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 9,
      handle: "proof-only",
      platform: "youtube",
      qualification_evidence: strictProof({
        followers: { value: 9600, minimum: 3000, known: true, passed: true },
      }),
    }]));

    expect(summary.rows[0].followers).toBe(9600);
  });
});

describe("search display cache privacy", () => {
  it("removes nested contact values but preserves status and counts", () => {
    const safe = sanitizeSearchDisplayForCache({
      input: "85mm portrait",
      activeSearchSession: {
        items: [{
          payload: {
            handle: "creator",
            email: "private@example.com",
            phone_number: "+1 555 0100",
            contact_channels: { whatsapp: "+1 555 0100" },
            contact_value: "private@example.com",
            other_contacts: [{ type: "telegram", value: "private-handle" }],
            contact_links_json: [{ type: "website", value: "https://private.example" }],
            contact_raw_json: { email: "private@example.com" },
            public_contact_value: "private@example.com",
            business_contact: "private@example.com",
            candidateContact: "+1 555 0100",
            external_contact: "private-handle",
            contactEmail: "private@example.com",
            contactUrl: "https://private.example",
            contactinfo: "private@example.com",
            lineId: "private_line_id",
            contact_preview: { status: "ready", channel_count: 2, email: "p***@example.com" },
            contact_enrichment: { status: "ready", count: 2, raw_value: "private@example.com" },
          },
        }],
      },
    });

    const payload = (safe as any).activeSearchSession.items[0].payload;
    expect(payload.handle).toBe("creator");
    expect(payload.email).toBeUndefined();
    expect(payload.phone_number).toBeUndefined();
    expect(payload.contact_channels).toBeUndefined();
    expect(payload.contact_value).toBeUndefined();
    expect(payload.other_contacts).toBeUndefined();
    expect(payload.contact_links_json).toBeUndefined();
    expect(payload.contact_raw_json).toBeUndefined();
    expect(payload.public_contact_value).toBeUndefined();
    expect(payload.business_contact).toBeUndefined();
    expect(payload.candidateContact).toBeUndefined();
    expect(payload.external_contact).toBeUndefined();
    expect(payload.contactEmail).toBeUndefined();
    expect(payload.contactUrl).toBeUndefined();
    expect(payload.contactinfo).toBeUndefined();
    expect(payload.lineId).toBeUndefined();
    expect(payload.contact_preview).toEqual({ status: "ready", channel_count: 2 });
    expect(payload.contact_enrichment).toEqual({ status: "ready", count: 2 });
  });

  it("cleans contact values left by an older cache version when it is read", () => {
    window.sessionStorage.setItem("vkpi:activeKolSearchDisplay", JSON.stringify({
      input: "portrait",
      mode: "text",
      recallResult: { items: [{ source_fields: { email: "old@example.com", contact_status: "ready" } }] },
      urlResult: null,
      activeSearchSession: null,
      activeSearchSessionId: null,
    }));

    const restored = readPersistedSearchDisplay() as any;
    expect(restored.recallResult.items[0].source_fields).toEqual({ contact_status: "ready" });
    expect(window.sessionStorage.getItem("vkpi:activeKolSearchDisplay")).not.toContain("old@example.com");
  });

  it("redacts contact values embedded in ordinary strings but preserves public copy and profile URLs", () => {
    const safe = sanitizeSearchDisplayForCache({
      bio: "Camera creator. Email private@example.com or call +1 (555) 010-2020 for work.",
      messenger: "WhatsApp: @private_handle / Telegram: @private_handle",
      mail_route: "mailto:private@example.com",
      tel_route: "tel:+15550102020",
      social_route_a: "https://wa.me/15550102020",
      social_route_b: "https://t.me/private_handle",
      candidate_link: "https://creator.example/contact",
      publicBio: "Camera reviews, portrait tutorials, and weekly field tests.",
      profile_url: "https://www.youtube.com/@public_creator",
      handle: "@public_creator",
    }) as any;

    expect(safe.bio).toContain("Camera creator.");
    expect(safe.bio).not.toContain("private@example.com");
    expect(safe.bio).not.toContain("555");
    expect(safe.messenger).toBeUndefined();
    expect(safe.mail_route).toBe("");
    expect(safe.tel_route).toBe("");
    expect(safe.social_route_a).toBe("");
    expect(safe.social_route_b).toBe("");
    expect(safe.candidate_link).toBe("");
    expect(safe.publicBio).toBe("Camera reviews, portrait tutorials, and weekly field tests.");
    expect(safe.profile_url).toBe("https://www.youtube.com/@public_creator");
    expect(safe.handle).toBe("@public_creator");
  });

  it("redacts nested social DM routes and contact URLs without erasing safe platform copy", () => {
    const safe = sanitizeSearchDisplayForCache({
      profile_flow: {
        profile_data: {
          bio: [
            "Camera creator.", "Messenger: private_handle", "DM me on Instagram @igprivate",
            "message me on TikTok @tikprivate", "Facebook DM @fbprivate",
            "Twitter @xprivate message me", "@reverseprivate on X DM me",
          ].join(" "),
          safe_copy: "Messenger app review. Follow @creator on Instagram for reviews.",
          instagram_profile: "https://www.instagram.com/public_creator/",
        },
      },
      routes: [
        "https://m.me/private", "https://line.me/R/ti/p/~private", "https://signal.me/#p/+15550100",
        "https://discord.gg/private", "https://discord.com/invite/private", "https://discord.com/users/123",
        "https://discord.com/channels/@me/123", "https://instagram.com/direct/t/123",
        "https://x.com/messages/compose?recipient_id=1", "https://twitter.com/messages/123",
        "https://facebook.com/messages/t/123", "sms:+15550100",
      ],
    }) as any;

    const profile = safe.profile_flow.profile_data;
    expect(profile.bio).toBe("Camera creator.");
    expect(profile.safe_copy).toBe("Messenger app review. Follow @creator on Instagram for reviews.");
    expect(profile.instagram_profile).toBe("https://www.instagram.com/public_creator/");
    expect(safe.routes).toEqual(Array(12).fill(""));
  });

  it("keeps only bounded enum, boolean, and numeric values in contact status containers", () => {
    const safe = sanitizeSearchDisplayForCache({
      contact_preview: {
        status: "ready",
        state: "mailto:private@example.com",
        score: "private@example.com",
        channel_count: 2,
        count: 1_000_001,
        available: true,
        note: "private@example.com",
      },
    }) as any;

    expect(safe.contact_preview).toEqual({ status: "ready", channel_count: 2, available: true });
  });
});
