import { useEffect, useMemo, useState } from 'react';
import { lookupCachedVideoUrl } from '../../../services/vkpi.ui-api';

const IMAGE_PROXY_HOSTS = [
  'cdninstagram.com',
  'fbcdn.net',
  'xx.fbcdn.net',
  'ytimg.com',
  'googleusercontent.com',
  'tiktokcdn.com',
  'tiktokcdn-us.com',
  'byteoversea.com',
  'apifyusercontent.com',
  'redd.it',
  'redditmedia.com',
  'twimg.com',
];

const VIDEO_PROXY_HOSTS = [
  'cdninstagram.com',
  'fbcdn.net',
  'xx.fbcdn.net',
  'tiktokcdn.com',
  'tiktokcdn-us.com',
  'byteoversea.com',
  'akamaized.net',
  'googlevideo.com',
  'apifyusercontent.com',
  'redd.it',
  'redditmedia.com',
  'twimg.com',
];
const CACHED_VIDEO_LOOKUP_PLATFORMS = new Set(['instagram', 'tiktok']);
const VIDEO_LOOKUP_TTL_MS = 60_000;

type VideoLookupEntry = {
  value: string;
  checkedAt: number;
};

const videoLookupCache = new Map<string, VideoLookupEntry>();
const videoLookupInflight = new Map<string, Promise<string>>();

function hostMatches(host: string, suffixes: string[]) {
  return suffixes.some((suffix) => host === suffix || host.endsWith(`.${suffix}`));
}

function localOrBlobUrl(url: string) {
  return url.startsWith('/') || url.startsWith('data:') || url.startsWith('blob:');
}

function cachedVideoLookupKey(platform: string, videoId: string) {
  return `${platform.trim().toLowerCase()}:${videoId.trim()}`;
}

function shouldLookupCachedVideo(platform: string, videoId: string) {
  return CACHED_VIDEO_LOOKUP_PLATFORMS.has(platform.trim().toLowerCase()) && Boolean(videoId.trim());
}

export function proxiedImageUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || localOrBlobUrl(url)) return url;
  try {
    const parsed = new URL(url);
    if (hostMatches(parsed.hostname.toLowerCase(), IMAGE_PROXY_HOSTS)) {
      return `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
}

export function proxiedVideoUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || localOrBlobUrl(url)) return url;
  try {
    const parsed = new URL(url);
    if (hostMatches(parsed.hostname.toLowerCase(), VIDEO_PROXY_HOSTS)) {
      return `/api/admin/vkpi/media/video-proxy?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
}

export function useCachedVideoUrl(apiToken: string | undefined, platform: string, videoId: string, rawFallbackUrl: unknown): string {
  const fallbackUrl = useMemo(() => proxiedVideoUrl(rawFallbackUrl), [rawFallbackUrl]);
  const normalizedPlatform = String(platform || '').trim().toLowerCase();
  const normalizedVideoId = String(videoId || '').trim();
  const lookupKey = cachedVideoLookupKey(normalizedPlatform, normalizedVideoId);
  const [resolvedUrl, setResolvedUrl] = useState(fallbackUrl);

  useEffect(() => {
    let cancelled = false;
    setResolvedUrl(fallbackUrl);
    if (!apiToken || !fallbackUrl || !shouldLookupCachedVideo(normalizedPlatform, normalizedVideoId)) {
      return () => {
        cancelled = true;
      };
    }

    const cached = videoLookupCache.get(lookupKey);
    if (cached && Date.now() - cached.checkedAt < VIDEO_LOOKUP_TTL_MS) {
      if (cached.value) setResolvedUrl(cached.value);
      return () => {
        cancelled = true;
      };
    }

    let lookup = videoLookupInflight.get(lookupKey);
    if (!lookup) {
      lookup = lookupCachedVideoUrl(apiToken, normalizedPlatform, normalizedVideoId)
        .then((value) => {
          videoLookupCache.set(lookupKey, { value, checkedAt: Date.now() });
          return value;
        })
        .catch(() => {
          videoLookupCache.set(lookupKey, { value: '', checkedAt: Date.now() });
          return '';
        })
        .finally(() => {
          videoLookupInflight.delete(lookupKey);
        });
      videoLookupInflight.set(lookupKey, lookup);
    }

    lookup.then((value) => {
      if (!cancelled && value) setResolvedUrl(value);
    });
    return () => {
      cancelled = true;
    };
  }, [apiToken, fallbackUrl, lookupKey, normalizedPlatform, normalizedVideoId]);

  return resolvedUrl;
}

export function redirectedVideoUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || localOrBlobUrl(url)) return url;
  try {
    const parsed = new URL(url);
    if (hostMatches(parsed.hostname.toLowerCase(), VIDEO_PROXY_HOSTS)) {
      return `/api/admin/vkpi/media/video-redirect?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
}

export function playbackVideoCandidates(rawUrls: unknown[]): string[] {
  const candidates: string[] = [];
  const seen = new Set<string>();
  const push = (value: unknown) => {
    const url = String(value || '').trim();
    if (!url || seen.has(url)) return;
    seen.add(url);
    candidates.push(url);
  };

  for (const rawUrl of rawUrls) {
    push(proxiedVideoUrl(rawUrl));
    push(redirectedVideoUrl(rawUrl));
  }
  return candidates;
}

export function platformExternalUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  return /^https?:\/\//i.test(url) ? url : '';
}

export function likelyVideoUrl(rawUrl: unknown, platform = ''): boolean {
  const url = String(rawUrl || '').trim().toLowerCase();
  if (!url) return false;
  if (url.startsWith('/api/admin/vkpi/media/video-proxy')) return true;
  if (url.startsWith('/api/vkpi-media/video-cache') || url.startsWith('/api/admin/vkpi/media/video-cache')) return true;
  if (url.includes('.jpg') || url.includes('.jpeg') || url.includes('.png') || url.includes('.webp') || url.includes('.image')) {
    return false;
  }
  return url.includes('.mp4') || url.includes('/video/') || url.includes('googlevideo.com') || url.includes('v.redd.it') || url.includes('video.twimg.com') || (platform.toLowerCase() === 'tiktok' && url.includes('tiktok.com/@'));
}
