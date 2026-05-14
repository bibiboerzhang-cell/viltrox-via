import type { Row } from './types';
import { rowString } from './rowAccessors';

export const POST_THUMBNAIL_KEYS = [
  'thumbnail_url',
  'thumbnail',
  'thumbnailUrl',
  'cover_url',
  'coverUrl',
  'image_url',
  'imageUrl',
  'display_url',
  'displayUrl',
  'display_url_hd',
  'displayUrlHD',
  'video_cover_url',
  'videoCoverUrl',
  'preview_url',
  'previewUrl',
  'poster_url',
  'posterUrl',
];

export const POST_VIDEO_KEYS = [
  'video_url',
  'videoUrl',
  'videoUrlNoWaterMark',
  'video_url_no_watermark',
  'video_download_url',
  'videoDownloadUrl',
  'downloadUrl',
  'downloadAddr',
  'media_url',
  'mediaUrl',
  'play_url',
  'playUrl',
  'url_to_video',
  'source_video_url',
];

export const POST_PLATFORM_URL_KEYS = [
  'post_url',
  'postUrl',
  'webVideoUrl',
  'permalink_url',
  'permalinkUrl',
  'permalink',
  'shortCodeUrl',
  'external_url',
  'link',
  'url',
];

export const ACCOUNT_AVATAR_KEYS = [
  'avatar_url',
  'avatarUrl',
  'profile_pic_url',
  'profilePicUrl',
  'profile_pic_url_hd',
  'profilePicUrlHD',
  'profile_image_url',
  'profileImageUrl',
  'image_url',
  'imageUrl',
  'picture',
];

export const ACCOUNT_PROFILE_URL_KEYS = [
  'profile_url',
  'profileUrl',
  'platform_url',
  'homepage_url',
  'inputUrl',
  'url',
];

const RAW_JSON_KEYS = [
  'raw_platform_data',
  'rawData',
  'raw_data',
  'raw_data_json',
  'raw_json',
  'metadata_json',
  'metadata',
];

function parseRawValue(value: unknown): unknown {
  if (!value) return null;
  if (typeof value === 'object') return value;
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || !/^[\[{]/.test(text)) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function findNestedString(value: unknown, keys: string[], depth = 0): string {
  if (!value || depth > 5) return '';
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findNestedString(item, keys, depth + 1);
      if (found) return found;
    }
    return '';
  }
  if (typeof value !== 'object') return '';
  const row = value as Row;
  const direct = rowString(row, keys);
  if (direct) return direct;
  for (const item of Object.values(row)) {
    const found = findNestedString(item, keys, depth + 1);
    if (found) return found;
  }
  return '';
}

function mediaString(row: Row | null | undefined, keys: string[]): string {
  const direct = rowString(row, keys);
  if (direct || !row) return direct;
  for (const key of RAW_JSON_KEYS) {
    const parsed = parseRawValue(row[key]);
    const found = findNestedString(parsed, keys);
    if (found) return found;
  }
  return '';
}

export function postThumbnailUrl(post: Row | null | undefined): string {
  return mediaString(post, POST_THUMBNAIL_KEYS);
}

export function postVideoUrl(post: Row | null | undefined): string {
  return mediaString(post, POST_VIDEO_KEYS);
}

export function postPlatformUrl(post: Row | null | undefined): string {
  return mediaString(post, POST_PLATFORM_URL_KEYS);
}

export function accountAvatarUrl(account: Row | null | undefined): string {
  return mediaString(account, ACCOUNT_AVATAR_KEYS);
}

export function accountProfileUrl(account: Row | null | undefined): string {
  return mediaString(account, ACCOUNT_PROFILE_URL_KEYS);
}
