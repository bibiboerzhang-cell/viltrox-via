export type KolContactChannel = {
  type: string;
  label: string;
  value: string;
  href?: string;
  masked: boolean;
};

const INTERNAL_KEYS = new Set([
  "type",
  "contact_type",
  "platform",
  "label",
  "source",
  "source_type",
  "contact_source",
  "source_url",
  "evidence",
  "confidence",
  "consent_basis",
  "is_public_declared",
  "extracted_by_staff_id",
  "created_at",
  "updated_at",
  "first_seen_at",
  "last_seen_at",
  "masked_value",
]);

const TYPE_LABELS: Record<string, string> = {
  email: "邮箱",
  business_email: "商务邮箱",
  phone: "电话",
  contact_phone: "电话",
  phone_number: "电话",
  mobile: "手机",
  whatsapp: "WhatsApp",
  ig_dm: "Instagram",
  instagram: "Instagram",
  tt_dm: "TikTok",
  tiktok: "TikTok",
  yt_about: "YouTube",
  youtube: "YouTube",
  x_dm: "X",
  twitter: "X",
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  link: "其他渠道",
  website: "网站",
};

function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const text = value.trim();
  if (!text || (!text.startsWith("[") && !text.startsWith("{"))) return value;
  try { return JSON.parse(text); } catch { return null; }
}

function text(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

function safeHref(type: string, value: string, masked: boolean) {
  if (masked) return undefined;
  const lowerType = type.toLowerCase();
  if (["email", "business_email", "contact_email", "public_email"].includes(lowerType)) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? `mailto:${value}` : undefined;
  }
  if (["phone", "contact_phone", "phone_number", "mobile"].includes(lowerType)) {
    return `tel:${value.replace(/[^+\d]/g, "")}`;
  }
  if (lowerType === "whatsapp") {
    if (/^https:\/\/(?:wa\.me|api\.whatsapp\.com)\//i.test(value)) return value;
    const digits = value.replace(/\D/g, "");
    return digits ? `https://wa.me/${digits}` : undefined;
  }
  if (/^https:\/\//i.test(value)) return value;
  return undefined;
}

function inferType(type: string, value: string) {
  const normalized = type.trim().toLowerCase();
  if (normalized && normalized !== "value" && normalized !== "address") return normalized;
  if (value.includes("@")) return "email";
  if (/^https:\/\/wa\.me\//i.test(value)) return "whatsapp";
  if (/^https?:\/\//i.test(value)) return "link";
  return "phone";
}

function normalizeLabel(type: string, rawLabel = "") {
  const label = rawLabel.trim();
  if (label && !INTERNAL_KEYS.has(label.toLowerCase())) return TYPE_LABELS[label.toLowerCase()] || label;
  return TYPE_LABELS[type] || "其他渠道";
}

function pushContact(
  output: KolContactChannel[],
  seen: Set<string>,
  rawType: string,
  rawValue: unknown,
  rawLabel = "",
) {
  const value = text(rawValue);
  if (!value || value === "[]" || value === "{}" || value === "null") return;
  const type = inferType(rawType, value);
  const key = value.toLowerCase();
  if (seen.has(key)) return;
  seen.add(key);
  const masked = value.includes("*");
  output.push({ type, label: normalizeLabel(type, rawLabel), value, href: safeHref(type, value, masked), masked });
}

function visit(output: KolContactChannel[], seen: Set<string>, raw: unknown, typeHint = "") {
  const value = parseJson(raw);
  if (Array.isArray(value)) {
    value.forEach((entry) => visit(output, seen, entry, typeHint));
    return;
  }
  if (!value || typeof value !== "object") {
    if (typeof value === "string" && value.includes(":")) {
      const [prefix, ...rest] = value.split(":");
      const knownPrefix = TYPE_LABELS[prefix.toLowerCase()];
      if (knownPrefix && rest.length) {
        pushContact(output, seen, prefix, rest.join(":"));
        return;
      }
    }
    pushContact(output, seen, typeHint, value);
    return;
  }
  const record = value as Record<string, unknown>;
  const platform = text(record.platform);
  const recordType = text(platform || record.contact_type || record.type || typeHint);
  const recordValue = record.contact_value ?? record.value ?? record.address ?? record.url ?? record.href ?? record.handle;
  if (recordValue !== undefined && typeof recordValue !== "object") {
    pushContact(output, seen, recordType, recordValue, text(record.label || platform));
    return;
  }
  Object.entries(record).forEach(([key, entry]) => {
    if (INTERNAL_KEYS.has(key.toLowerCase())) return;
    visit(output, seen, entry, key);
  });
}

export function kolContactChannels(item: Record<string, unknown> | null | undefined): KolContactChannel[] {
  const source = item && typeof item === "object" ? item : {};
  const output: KolContactChannel[] = [];
  const seen = new Set<string>();
  ["email", "business_email", "public_email", "contact_email"].forEach((key) => {
    pushContact(output, seen, "email", source[key]);
  });
  ["phone", "contact_phone", "phone_number", "mobile", "whatsapp"].forEach((key) => {
    pushContact(output, seen, key, source[key]);
  });
  ["other_contacts", "other_contacts_json", "contact_channels", "contact_links", "contact_links_json"].forEach((key) => {
    visit(output, seen, source[key], key);
  });
  return output;
}
