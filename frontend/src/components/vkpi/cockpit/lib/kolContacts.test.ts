import { describe, expect, it } from "vitest";

import { contactStateFromReveal, contactTier, kolContactChannels } from "./kolContacts";

describe("kolContactChannels", () => {
  it("labels manual social handles without turning them into email links", () => {
    const contacts = kolContactChannels({
      other_contacts_json: JSON.stringify([
        { contact_type: "link", platform: "ig_dm", contact_value: "@creator", contact_source: "manual" },
      ]),
    });

    expect(contacts).toEqual([expect.objectContaining({
      type: "instagram_dm",
      label: "Instagram",
      value: "@creator",
      href: "https://www.instagram.com/creator/",
      action: "dm",
      actionLabel: "发起 Instagram DM",
      masked: false,
    })]);
  });

  it("builds only safe actionable links, deduplicates, and ignores metadata", () => {
    const contacts = kolContactChannels({
      email: "manager@example.com",
      contact_phone: "+1 (202) 555-0199",
      other_contacts_json: [
        { contact_type: "email", contact_value: "manager@example.com", confidence: 0.9 },
        { contact_type: "website", contact_value: "https://creator.example/contact", source: "public" },
        { contact_type: "link", contact_value: "javascript:alert(1)" },
      ],
    });

    expect(contacts.find((entry) => entry.type === "email")?.href).toBe("mailto:manager@example.com");
    expect(contacts.find((entry) => entry.type === "phone")?.href).toBe("tel:+12025550199");
    expect(contacts.find((entry) => entry.type === "website")?.href).toBe("https://creator.example/contact");
    expect(contacts.find((entry) => entry.value.startsWith("javascript:"))?.href).toBeUndefined();
    expect(contacts.filter((entry) => entry.value === "manager@example.com")).toHaveLength(1);
    expect(JSON.stringify(contacts)).not.toContain("confidence");
    expect(contacts.find((entry) => entry.type === "website")?.source).toBe("public");
  });

  it("drops masked strings because they are availability hints, not contact values", () => {
    const contacts = kolContactChannels({
      contact_masked: true,
      email: "m***@e***",
      other_contacts_json: [{ contact_type: "whatsapp", contact_value: "+1*******99" }],
    });

    expect(contacts).toEqual([]);
  });

  it("ignores migration-only masked_value beside a real full contact", () => {
    const contacts = kolContactChannels({
      email: "manager@example.com",
      contact_channels: {
        email: { type: "email", masked_value: "m***@e***", source: "migration_214" },
      },
    });

    expect(contacts).toEqual([expect.objectContaining({ value: "manager@example.com", masked: false })]);
    expect(JSON.stringify(contacts)).not.toContain("m***@e***");
  });

  it("maps typed phone, WhatsApp, DM, marketplace and website-form contacts to the right CTA", () => {
    const state = contactStateFromReveal({
      status: "full",
      contact_masked: false,
      contacts: [
        { type: "phone", value: "+1 (202) 555-0199" },
        { type: "whatsapp", value: "+12025550188" },
        { type: "dm", channel: "instagram", value: "@framecraft" },
        { type: "marketplace_dm", value: "https://market.example/creator/framecraft/messages" },
        { type: "website_form", value: "https://framecraft.example/contact" },
      ],
    });

    expect(state.status).toBe("full");
    expect(state.contacts).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: "phone", actionLabel: "拨打电话", href: "tel:+12025550199" }),
      expect.objectContaining({ type: "whatsapp", actionLabel: "打开 WhatsApp", href: "https://wa.me/12025550188" }),
      expect.objectContaining({ type: "instagram_dm", actionLabel: "发起 Instagram DM", href: "https://www.instagram.com/framecraft/" }),
      expect.objectContaining({ type: "marketplace_dm", actionLabel: "打开 Marketplace 私信" }),
      expect.objectContaining({ type: "website_form", actionLabel: "打开联系表单" }),
    ]));
  });

  it("never guesses an unknown handle is a phone number", () => {
    const [contact] = kolContactChannels({
      contacts: [{ type: "contact", value: "creator-support-id" }],
    });

    expect(contact).toEqual(expect.objectContaining({ type: "contact", href: undefined, action: "copy" }));
  });

  it("keeps masked blank-email responses restricted instead of calling them empty", () => {
    const state = contactStateFromReveal({
      status: "restricted",
      contact_masked: true,
      email: "",
      contacts: [],
      reason: "sensitive_access_audit_unavailable",
    });

    expect(state).toEqual({
      status: "restricted",
      contacts: [],
      reason: "sensitive_access_audit_unavailable",
    });
  });

  it("rejects redacted values even when a rolling response incorrectly says full", () => {
    const state = contactStateFromReveal({
      status: "full",
      contact_masked: false,
      contacts: [{ type: "email", value: "m***@e***" }],
    });

    expect(state.status).toBe("restricted");
    expect(JSON.stringify(state)).not.toContain("m***@e***");
  });

  it("parses the legacy revealed email/other_contacts response", () => {
    const state = contactStateFromReveal({
      status: "revealed",
      contact_masked: false,
      email: "manager@example.com",
      other_contacts: [{ contact_type: "link", platform: "ig_dm", contact_value: "@creator" }],
    });

    expect(state.status).toBe("full");
    expect(state.contacts.map((contact) => contact.type)).toEqual(["email", "instagram_dm"]);
    expect(state.contacts.every((contact) => contact.tier === undefined)).toBe(true);
  });
});

describe("contact disclosure tiers", () => {
  it("resolves an explicit tier first and falls back to verification status", () => {
    expect(contactTier("verified", "observed")).toBe("verified");
    expect(contactTier("observed", "verified_public_business")).toBe("observed");
    expect(contactTier("OBSERVED")).toBe("observed");
    expect(contactTier("", "verified_public_business")).toBe("verified");
    expect(contactTier(undefined, "observed")).toBe("observed");
    expect(contactTier("gold", "stale")).toBeUndefined();
    expect(contactTier(undefined, undefined)).toBeUndefined();
  });

  it("carries verified/observed tiers through a typed reveal response", () => {
    const state = contactStateFromReveal({
      status: "full",
      contact_masked: false,
      verified_count: 1,
      observed_count: 2,
      contacts: [
        {
          id: 1,
          channel: "email",
          contact_type: "email",
          value: "biz@example.com",
          tier: "verified",
          verification_status: "verified_public_business",
          source_type: "youtube_about_declared",
          verified_at: "2026-08-15T01:00:00Z",
        },
        {
          id: 2,
          channel: "instagram_dm",
          contact_type: "instagram_dm",
          value: "@creator",
          tier: "observed",
          verification_status: "observed",
          source_type: "raw_bio_scan",
        },
        {
          id: 3,
          channel: "website",
          contact_type: "website",
          value: "https://creator.example/",
          tier: "observed",
          verification_status: "observed",
          source_type: "raw_full_scan",
        },
      ],
    });

    expect(state.status).toBe("full");
    expect(state.contacts.map((contact) => [contact.type, contact.tier])).toEqual([
      ["email", "verified"],
      ["instagram_dm", "observed"],
      ["website", "observed"],
    ]);
    expect(state.contacts[0].lastVerifiedAt).toBe("2026-08-15T01:00:00Z");
    expect(state.contacts[1].lastVerifiedAt).toBeUndefined();
    expect(state.contacts[1].href).toBe("https://www.instagram.com/creator/");
  });

  it("keeps the first (verified) copy when the same value is revealed twice", () => {
    const contacts = kolContactChannels({
      contacts: [
        { contact_type: "email", value: "biz@example.com", tier: "verified" },
        { contact_type: "email", value: "BIZ@example.com", tier: "observed" },
      ],
    });

    expect(contacts).toHaveLength(1);
    expect(contacts[0].tier).toBe("verified");
  });

  it("never turns the tier field into a contact row", () => {
    const contacts = kolContactChannels({
      contacts: [{ contact_type: "email", value: "biz@example.com", tier: "observed", source_type: "raw_bio_scan" }],
    });

    expect(contacts).toHaveLength(1);
    expect(contacts[0].value).toBe("biz@example.com");
    expect(JSON.stringify(contacts)).not.toContain("source_type");
  });

  it("still drops masked observed-tier values from a rolling response", () => {
    const state = contactStateFromReveal({
      status: "full",
      contact_masked: false,
      contacts: [{ contact_type: "email", value: "b***@e***", tier: "observed" }],
    });

    expect(state.status).toBe("restricted");
    expect(state.contacts).toEqual([]);
    expect(JSON.stringify(state)).not.toContain("b***@e***");
  });
});
