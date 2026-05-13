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

export function postThumbnailUrl(post: Row | null | undefined): string {
  return rowString(post, POST_THUMBNAIL_KEYS);
}

export function postVideoUrl(post: Row | null | undefined): string {
  return rowString(post, POST_VIDEO_KEYS);
}

export function postPlatformUrl(post: Row | null | undefined): string {
  return rowString(post, POST_PLATFORM_URL_KEYS);
}

export function accountAvatarUrl(account: Row | null | undefined): string {
  return rowString(account, ACCOUNT_AVATAR_KEYS);
}

export function accountProfileUrl(account: Row | null | undefined): string {
  return rowString(account, ACCOUNT_PROFILE_URL_KEYS);
}
