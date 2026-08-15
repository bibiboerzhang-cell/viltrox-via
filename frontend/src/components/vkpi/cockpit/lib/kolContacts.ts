export type KolContactAction =
  | "email"
  | "phone"
  | "whatsapp"
  | "dm"
  | "marketplace"
  | "website_form"
  | "website"
  | "copy";

export type KolContactChannel = {
  type: string;
  label: string;
  value: string;
  href?: string;
  action: KolContactAction;
  actionLabel: string;
  masked: boolean;
  source?: string;
  verificationStatus?: string;
  lastVerifiedAt?: string;
};

export type ContactPurpose = "kol_detail_view" | "compose_outreach";

export type ContactState = {
  contacts: KolContactChannel[];
  reason?: string;
  message?: string;
  auditedPurpose?: ContactPurpose;
  auditedKolPoolId?: string;
} & (
  | { status: "loading" }
  | { status: "full" }
  | { status: "restricted" }
  | { status: "empty" }
  | { status: "error" }
);

const INTERNAL_KEYS = new Set([
  "type",
  "kind",
  "channel",
  "contact_type",
  "platform",
  "label",
  "source",
  "source_kind",
  "source_label",
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
  "last_verified_at",
  "verified_at",
  "verification_status",
  "status",
  "reason",
  "masked_value",
  "action_url",
  "href",
]);

const TYPE_ALIASES: Record<string, string> = {
  email: "email",
  business_email: "email",
  contact_email: "email",
  public_email: "email",
  phone: "phone",
  contact_phone: "phone",
  phone_number: "phone",
  mobile: "phone",
  telephone: "phone",
  whatsapp: "whatsapp",
  whatsapp_link: "whatsapp",
  wa: "whatsapp",
  ig_dm: "instagram_dm",
  instagram_dm: "instagram_dm",
  instagram_link: "instagram_dm",
  instagram: "instagram_dm",
  tt_dm: "tiktok_dm",
  tiktok_dm: "tiktok_dm",
  tiktok_link: "tiktok_dm",
  tiktok: "tiktok_dm",
  x_dm: "x_dm",
  twitter_dm: "x_dm",
  twitter_link: "x_dm",
  twitter: "x_dm",
  marketplace: "marketplace_dm",
  marketplace_dm: "marketplace_dm",
  official_marketplace: "marketplace_dm",
  website_form: "website_form",
  contact_form: "website_form",
  web_form: "website_form",
  website: "website",
  yt_about: "youtube_about",
  youtube_about: "youtube_about",
  youtube_link: "youtube_about",
  youtube: "youtube_about",
  wechat: "wechat",
  discord: "discord",
  telegram: "telegram",
  link: "link",
  url: "link",
  contact: "contact",
};

const TYPE_LABELS: Record<string, string> = {
  email: "邮箱",
  phone: "电话",
  whatsapp: "WhatsApp",
  instagram_dm: "Instagram",
  tiktok_dm: "TikTok",
  x_dm: "X",
  marketplace_dm: "Marketplace",
  website_form: "联系表单",
  website: "官网",
  youtube_about: "YouTube",
  wechat: "WeChat",
  discord: "Discord",
  telegram: "Telegram",
  link: "其他渠道",
  contact: "其他渠道",
};

function parseJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const valueText = value.trim();
  if (!valueText || (!valueText.startsWith("[") && !valueText.startsWith("{"))) return value;
  try { return JSON.parse(valueText); } catch { return null; }
}

function text(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

function safeHttps(value: string) {
  if (!/^https:\/\//i.test(value)) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.toString() : undefined;
  } catch {
    return undefined;
  }
}

function socialHandle(value: string) {
  const handle = value.trim().replace(/^@/, "");
  return /^[A-Za-z0-9._-]{1,64}$/.test(handle) ? handle : "";
}

function canonicalType(rawType: string, platform: string, value: string) {
  const normalizedType = rawType.trim().toLowerCase().replace(/[\s.-]+/g, "_");
  const normalizedPlatform = platform.trim().toLowerCase().replace(/[\s.-]+/g, "_");
  if (normalizedType === "dm") {
    return TYPE_ALIASES[`${normalizedPlatform}_dm`] || TYPE_ALIASES[normalizedPlatform] || "contact";
  }
  if ((!normalizedType || normalizedType === "value" || normalizedType === "address" || normalizedType === "link") && normalizedPlatform) {
    return TYPE_ALIASES[normalizedPlatform] || TYPE_ALIASES[`${normalizedPlatform}_dm`] || TYPE_ALIASES[normalizedType] || "contact";
  }
  const explicit = TYPE_ALIASES[normalizedType];
  if (explicit) return explicit;
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return "email";
  if (/^https:\/\/(?:wa\.me|api\.whatsapp\.com)\//i.test(value)) return "whatsapp";
  if (/^https?:\/\//i.test(value)) return "link";
  // Unknown values stay non-actionable. Never turn a DM handle or arbitrary ID into a phone number.
  return normalizedType || "contact";
}

function contactAction(type: string, value: string): Pick<KolContactChannel, "href" | "action" | "actionLabel"> {
  if (type === "email") {
    const href = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? `mailto:${value}` : undefined;
    return { href, action: "email", actionLabel: "发送邮件" };
  }
  if (type === "phone") {
    const normalized = value.replace(/[^+\d]/g, "");
    const href = /^\+?\d{7,15}$/.test(normalized) ? `tel:${normalized}` : undefined;
    return { href, action: "phone", actionLabel: "拨打电话" };
  }
  if (type === "whatsapp") {
    const direct = safeHttps(value);
    const digits = value.replace(/\D/g, "");
    const href = direct && /^https:\/\/(?:wa\.me|api\.whatsapp\.com)\//i.test(direct)
      ? direct
      : digits.length >= 7 && digits.length <= 15 ? `https://wa.me/${digits}` : undefined;
    return { href, action: "whatsapp", actionLabel: "打开 WhatsApp" };
  }
  if (type === "instagram_dm" || type === "tiktok_dm" || type === "x_dm") {
    const direct = safeHttps(value);
    const handle = socialHandle(value);
    const href = direct || (
      handle
        ? type === "instagram_dm"
          ? `https://www.instagram.com/${handle}/`
          : type === "tiktok_dm"
            ? `https://www.tiktok.com/@${handle}`
            : `https://x.com/${handle}`
        : undefined
    );
    const platform = type === "instagram_dm" ? "Instagram" : type === "tiktok_dm" ? "TikTok" : "X";
    return { href, action: "dm", actionLabel: `发起 ${platform} DM` };
  }
  if (type === "marketplace_dm") {
    return { href: safeHttps(value), action: "marketplace", actionLabel: "打开 Marketplace 私信" };
  }
  if (type === "website_form") {
    return { href: safeHttps(value), action: "website_form", actionLabel: "打开联系表单" };
  }
  if (type === "website" || type === "youtube_about" || type === "link") {
    return {
      href: safeHttps(value),
      action: type === "website" || type === "youtube_about" ? "website" : "copy",
      actionLabel: type === "website" ? "打开官网" : type === "youtube_about" ? "打开 YouTube About" : "打开链接",
    };
  }
  if (type === "telegram") {
    const direct = safeHttps(value);
    const handle = socialHandle(value);
    return { href: direct || (handle ? `https://t.me/${handle}` : undefined), action: "dm", actionLabel: "发起 Telegram 联系" };
  }
  return { href: safeHttps(value), action: "copy", actionLabel: "复制联系方式" };
}

function normalizeLabel(type: string, rawLabel = "") {
  const label = rawLabel.trim();
  if (label && !INTERNAL_KEYS.has(label.toLowerCase())) return TYPE_LABELS[TYPE_ALIASES[label.toLowerCase()] || label.toLowerCase()] || label;
  return TYPE_LABELS[type] || "其他渠道";
}

type ContactMetadata = {
  platform?: string;
  source?: string;
  verificationStatus?: string;
  lastVerifiedAt?: string;
};

function pushContact(
  output: KolContactChannel[],
  seen: Set<string>,
  rawType: string,
  rawValue: unknown,
  rawLabel = "",
  metadata: ContactMetadata = {},
) {
  const value = text(rawValue);
  if (!value || value === "[]" || value === "{}" || value === "null") return;
  const masked = value.includes("*");
  // Redacted strings are availability hints, not contact values. Never surface them as actionable or visible contact rows.
  if (masked) return;
  const type = canonicalType(rawType, metadata.platform || "", value);
  const key = `${type}:${value.toLowerCase()}`;
  if (seen.has(key)) return;
  seen.add(key);
  output.push({
    type,
    label: normalizeLabel(type, rawLabel),
    value,
    ...contactAction(type, value),
    masked: false,
    ...(metadata.source ? { source: metadata.source } : {}),
    ...(metadata.verificationStatus ? { verificationStatus: metadata.verificationStatus } : {}),
    ...(metadata.lastVerifiedAt ? { lastVerifiedAt: metadata.lastVerifiedAt } : {}),
  });
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
      if (TYPE_ALIASES[prefix.toLowerCase()] && rest.length) {
        pushContact(output, seen, prefix, rest.join(":"));
        return;
      }
    }
    pushContact(output, seen, typeHint, value);
    return;
  }
  const record = value as Record<string, unknown>;
  const platform = text(record.platform || record.channel);
  const explicitType = text(record.contact_type || record.type || record.kind || typeHint);
  const recordType = explicitType === "link" && platform ? platform : explicitType;
  const recordValue = record.contact_value
    ?? record.display_value
    ?? record.value
    ?? record.address
    ?? record.url
    ?? record.handle;
  if (recordValue !== undefined && typeof recordValue !== "object") {
    pushContact(output, seen, recordType, recordValue, text(record.label || platform), {
      platform,
      source: text(record.source_label || record.source_kind || record.contact_source || record.source),
      verificationStatus: text(record.verification_status || record.status),
      lastVerifiedAt: text(record.last_verified_at || record.verified_at || record.last_seen_at),
    });
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
  ["contacts", "other_contacts", "other_contacts_json", "contact_channels", "contact_links", "contact_links_json"].forEach((key) => {
    visit(output, seen, source[key], key);
  });
  return output;
}

function containsMaskedValue(value: unknown): boolean {
  if (typeof value === "string") return value.includes("*");
  if (Array.isArray(value)) return value.some(containsMaskedValue);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !["reason", "message", "status"].includes(key.toLowerCase()))
    .some(([, entry]) => containsMaskedValue(entry));
}

export function contactStateFromReveal(payload: unknown): ContactState {
  if (!payload || typeof payload !== "object") {
    return { status: "error", contacts: [], reason: "invalid_contact_response", message: "联系方式响应无效" };
  }
  const response = payload as Record<string, unknown>;
  const rawStatus = text(response.status).toLowerCase();
  const reason = text(response.reason || response.contact_projection_reason);
  const contacts = kolContactChannels({
    contacts: response.contacts,
    email: response.email,
    other_contacts: response.other_contacts,
    other_contacts_json: response.other_contacts_json,
  });
  const masked = response.contact_masked === true || containsMaskedValue({
    contacts: response.contacts,
    email: response.email,
    other_contacts: response.other_contacts,
    other_contacts_json: response.other_contacts_json,
  });
  if (rawStatus === "restricted" || rawStatus === "masked" || masked) {
    return { status: "restricted", contacts: [], reason: reason || "contact_access_restricted" };
  }
  if (rawStatus === "empty") return { status: "empty", contacts: [], reason: reason || "no_verified_contacts" };
  if (rawStatus === "full" || rawStatus === "revealed") {
    return contacts.length > 0
      ? { status: "full", contacts, reason }
      : { status: "empty", contacts: [], reason: reason || "no_verified_contacts" };
  }
  return { status: "error", contacts: [], reason: reason || "invalid_contact_status", message: "联系方式读取失败" };
}

export function contactErrorState(error: unknown): ContactState {
  const status = Number((error as { status?: unknown } | null)?.status);
  if (status === 401 || status === 403) {
    return { status: "restricted", contacts: [], reason: "contact_access_restricted", message: "当前账号无法读取完整联系方式" };
  }
  if (status === 429) {
    return { status: "error", contacts: [], reason: "contact_rate_limited", message: "完整联系方式读取过于频繁，请稍后再试" };
  }
  return { status: "error", contacts: [], reason: "contact_read_failed", message: "完整联系方式读取失败，请稍后重试" };
}
