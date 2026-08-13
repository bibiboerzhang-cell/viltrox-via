import { describe, expect, it } from "vitest";

import { kolContactChannels } from "./kolContacts";

describe("kolContactChannels", () => {
  it("labels manual social handles without turning them into email links", () => {
    const contacts = kolContactChannels({
      other_contacts_json: JSON.stringify([
        { contact_type: "link", platform: "ig_dm", contact_value: "@creator", contact_source: "manual" },
      ]),
    });

    expect(contacts).toEqual([expect.objectContaining({
      type: "ig_dm",
      label: "Instagram",
      value: "@creator",
      href: undefined,
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
    expect(contacts.find((entry) => entry.type === "contact_phone")?.href).toBe("tel:+12025550199");
    expect(contacts.find((entry) => entry.type === "website")?.href).toBe("https://creator.example/contact");
    expect(contacts.find((entry) => entry.value.startsWith("javascript:"))?.href).toBeUndefined();
    expect(contacts.filter((entry) => entry.value === "manager@example.com")).toHaveLength(1);
    expect(JSON.stringify(contacts)).not.toContain("confidence");
    expect(JSON.stringify(contacts)).not.toContain("public");
  });

  it("keeps masked values non-actionable", () => {
    const contacts = kolContactChannels({
      contact_masked: true,
      email: "m***@e***",
      other_contacts_json: [{ contact_type: "whatsapp", contact_value: "+1*******99" }],
    });

    expect(contacts.every((entry) => entry.masked && entry.href === undefined)).toBe(true);
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
});
