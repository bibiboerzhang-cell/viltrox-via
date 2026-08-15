import { asRecord, type Row } from "./SmartKolInputPanel.helpers";

const PRIVATE_CONTACT_CACHE_KEYS = new Set([
  "email", "contact_email", "business_email", "outreach_email",
  "phone", "phone_number", "mobile", "telephone",
  "whatsapp", "whatsapp_number", "telegram", "wechat", "line_id",
  "contact_channels", "contact", "contact_value", "contact_values_json",
  "contact_details", "contact_info", "contact_links", "contact_links_json",
  "contact_raw", "contact_raw_json", "other_contacts", "other_contacts_json",
  "contacts", "contact_values",
]);
const PRIVATE_CONTACT_CANONICAL_KEYS = new Set(["lineid"]);

const CONTACT_STATUS_CONTAINER_KEYS = new Set(["contactpreview", "contactability", "contactenrichment"]);
const SAFE_CONTACT_METADATA_KEYS = new Set([
  "contactavailable", "contactstatus", "contactabilitystatus", "contactmasked",
  "contactcount", "contactlastverifiedat", "channelcount",
]);
const CONTACT_STATUS_ENUM = new Set([
  "yes", "no", "unknown", "ready", "verified", "available", "found",
  "complete", "completed", "done", "queued", "running", "processing",
  "pending", "enriching", "missing", "empty", "none", "no_contacts",
  "not_found", "unavailable", "failed", "error", "blocked",
]);

const EMAIL_RE = /[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}/gi;
const CONTACT_SCHEME_RE = /\b(?:mailto|tel|sms|whatsapp|tg):[^\s<>'"]+/gi;
const HTTP_URL_RE = /https?:\/\/[^\s<>'"]+/gi;
const PHONE_CANDIDATE_RE = /(?:\+?\d[\d().\s-]{5,}\d)/g;
const LABELED_MESSENGER_RE = /\b(?:whats?app|telegram)\s*[:：]\s*@?[a-z0-9_.+()-]{4,}/gi;

function contactRouteUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    if (["wa.me", "api.whatsapp.com", "web.whatsapp.com", "t.me", "telegram.me", "telegram.dog"].includes(host)) {
      return true;
    }
    const contactQuery = [...url.searchParams.keys()].some((key) =>
      ["contact", "email", "phone", "tel", "whatsapp", "telegram"].includes(key.toLowerCase()),
    );
    return contactQuery
      || /\/(?:contact|contacts|contact-us|contact-me|business-inquiries|collab|collaboration|direct|message|dm)(?:\/|$)/i.test(url.pathname);
  } catch {
    return false;
  }
}

function removePhoneCandidate(candidate: string): string {
  const compact = candidate.trim();
  const digits = compact.replace(/\D/g, "");
  if (digits.length < 7 || digits.length > 15) return candidate;
  if (/^\d{4}-\d{2}-\d{2}(?:$|T)/.test(compact)) return candidate;
  const phoneSignal = compact.startsWith("+")
    || /[()\s]/.test(compact)
    || (compact.includes("-") && !/^\d{4}-\d{2}-\d{2}$/.test(compact))
    || /^\d{9,15}$/.test(compact);
  return phoneSignal ? "" : candidate;
}

export function sanitizeSearchCacheString(value: string): string {
  return value
    .replace(CONTACT_SCHEME_RE, "")
    .replace(HTTP_URL_RE, (url) => (contactRouteUrl(url) ? "" : url))
    .replace(LABELED_MESSENGER_RE, "")
    .replace(EMAIL_RE, "")
    .replace(PHONE_CANDIDATE_RE, removePhoneCandidate)
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function privateContactCacheKey(key: string): boolean {
  const normalized = key.toLowerCase();
  const canonical = normalized.replace(/[^a-z0-9]/g, "");
  if (SAFE_CONTACT_METADATA_KEYS.has(canonical)) return false;
  return PRIVATE_CONTACT_CACHE_KEYS.has(normalized)
    || PRIVATE_CONTACT_CANONICAL_KEYS.has(canonical)
    || normalized.endsWith("_email")
    || normalized.endsWith("_phone")
    || normalized.endsWith("_mobile")
    || normalized.endsWith("_contact_value")
    || normalized.endsWith("_contact_values")
    || normalized.endsWith("_contact_link")
    || normalized.endsWith("_contact_links")
    || normalized.includes("contact_raw")
    || ["email", "phone", "mobile", "whatsapp", "telegram", "wechat", "contact"]
      .some((marker) => canonical.includes(marker));
}

function safeContactStatusContainer(value: unknown): Row {
  const source = asRecord(value);
  const safe: Row = {};
  for (const [key, raw] of Object.entries(source)) {
    const normalized = key.toLowerCase();
    if (["status", "state", "contact_available"].includes(normalized)) {
      if (typeof raw === "string" && CONTACT_STATUS_ENUM.has(raw.toLowerCase())) safe[key] = raw.toLowerCase();
      else if (typeof raw === "boolean" && normalized === "contact_available") safe[key] = raw;
      continue;
    }
    if (["async", "available", "contact_masked"].includes(normalized)) {
      if (typeof raw === "boolean") safe[key] = raw;
      continue;
    }
    if (["channel_count", "contact_count", "count"].includes(normalized)) {
      if (typeof raw === "number" && Number.isInteger(raw) && raw >= 0 && raw <= 1_000_000) safe[key] = raw;
      continue;
    }
    if (normalized === "score" && typeof raw === "number" && Number.isFinite(raw) && raw >= 0 && raw <= 100) {
      safe[key] = raw;
    }
  }
  return safe;
}

/** Strip contact values before sessionStorage while retaining bounded operational status. */
export function sanitizeSearchDisplayForCache<T>(value: T): T {
  if (typeof value === "string") return sanitizeSearchCacheString(value) as T;
  if (Array.isArray(value)) return value.map((item) => sanitizeSearchDisplayForCache(item)) as T;
  if (!value || typeof value !== "object") return value;
  const safe: Row = {};
  Object.entries(value as Row).forEach(([key, entry]) => {
    if (CONTACT_STATUS_CONTAINER_KEYS.has(key.toLowerCase().replace(/[^a-z0-9]/g, ""))) {
      safe[key] = safeContactStatusContainer(entry);
      return;
    }
    if (privateContactCacheKey(key)) return;
    safe[key] = sanitizeSearchDisplayForCache(entry);
  });
  return safe as T;
}
