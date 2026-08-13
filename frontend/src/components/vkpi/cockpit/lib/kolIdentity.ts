export type KolIdentityLike = Record<string, unknown> | null | undefined;

function cleanIdentity(value: unknown) {
  return String(value || "").trim();
}

/** YouTube channel ids are durable storage identities, not creator-facing names. */
export function isOpaqueKolChannelId(value: unknown, item: KolIdentityLike = {}) {
  const candidate = cleanIdentity(value).replace(/^@/, "");
  if (!candidate) return false;
  const row = item || {};
  const explicitIds = [row.channel_id, row.channelId, row.youtube_channel_id]
    .map((entry) => cleanIdentity(entry).replace(/^@/, ""))
    .filter(Boolean);
  return explicitIds.includes(candidate) || /^UC[A-Za-z0-9_-]{20,}$/.test(candidate);
}

/** Detect an opaque channel id embedded in a URL or other display string. */
export function containsOpaqueKolChannelId(value: unknown, item: KolIdentityLike = {}) {
  const candidate = cleanIdentity(value);
  if (!candidate) return false;
  const row = item || {};
  const explicitIds = [row.channel_id, row.channelId, row.youtube_channel_id]
    .map((entry) => cleanIdentity(entry).replace(/^@/, ""))
    .filter(Boolean);
  return explicitIds.some((id) => candidate.includes(id)) || /UC[A-Za-z0-9_-]{20,}/.test(candidate);
}

/** Human-facing identity. Opaque ids remain available on the row for keys/URLs only. */
export function kolHumanDisplayName(item: KolIdentityLike, fallback = "Creator") {
  const row = item || {};
  const candidates = [
    row.display_name, row.displayName, row.name, row.kol_name, row.kolName,
    row.channel_name, row.channelName, row.handle, row.kol_handle, row.kolHandle, row.username,
  ];
  return candidates
    .map(cleanIdentity)
    .find((candidate) => candidate && !containsOpaqueKolChannelId(candidate, row)) || fallback;
}

/** A public handle suitable for display, or empty when the value is an opaque id/duplicate. */
export function kolHumanPublicHandle(item: KolIdentityLike) {
  const row = item || {};
  const handle = [row.handle, row.kol_handle, row.kolHandle, row.username].map(cleanIdentity).find(Boolean) || "";
  if (!handle || isOpaqueKolChannelId(handle, row)) return "";
  const displayName = kolHumanDisplayName(row);
  if (handle.replace(/^@/, "").toLowerCase() === displayName.replace(/^@/, "").toLowerCase()) return "";
  return handle;
}

export function kolHumanIdentitySubtitle(item: KolIdentityLike) {
  const displayName = kolHumanDisplayName(item);
  const handle = kolHumanPublicHandle(item);
  return handle ? `${displayName} · ${handle}` : displayName;
}

const HUMAN_PLATFORM_NAMES: Record<string, string> = {
  youtube: "YouTube",
  instagram: "Instagram",
  tiktok: "TikTok",
  bilibili: "Bilibili",
  douyin: "抖音",
  xiaohongshu: "小红书",
  twitter: "X",
  x: "X",
};

/** Keep an opaque id in the href, while presenting a stable human-facing label. */
export function kolHumanProfileLinkLabel(item: KolIdentityLike, action = "打开") {
  const row = item || {};
  const platformKey = cleanIdentity(row.platform || row.kol_platform || row.source_platform).toLowerCase();
  const platform = HUMAN_PLATFORM_NAMES[platformKey];
  return [action, platform ? `${platform} 主页` : "创作者主页"].filter(Boolean).join(" ");
}
