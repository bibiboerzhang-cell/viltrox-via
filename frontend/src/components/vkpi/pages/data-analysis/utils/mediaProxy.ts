const IMAGE_PROXY_HOSTS = [
  'cdninstagram.com',
  'fbcdn.net',
  'xx.fbcdn.net',
  'ytimg.com',
  'googleusercontent.com',
];

const VIDEO_REDIRECT_HOSTS = [
  'cdninstagram.com',
  'fbcdn.net',
  'xx.fbcdn.net',
  'tiktokcdn.com',
  'tiktokcdn-us.com',
  'byteoversea.com',
  'akamaized.net',
  'googlevideo.com',
  'apifyusercontent.com',
];

export function proxiedImageUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || url.startsWith('/') || url.startsWith('data:') || url.startsWith('blob:')) return url;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (IMAGE_PROXY_HOSTS.some((suffix) => host === suffix || host.endsWith(`.${suffix}`))) {
      return `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
}

export function proxiedVideoUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || url.startsWith('/') || url.startsWith('data:') || url.startsWith('blob:')) return url;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (VIDEO_REDIRECT_HOSTS.some((suffix) => host === suffix || host.endsWith(`.${suffix}`))) {
      return `/api/admin/vkpi/media/video-proxy?url=${encodeURIComponent(url)}`;
    }
  } catch {
    return url;
  }
  return url;
}

export function redirectedVideoUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  if (!url || url.startsWith('/') || url.startsWith('data:') || url.startsWith('blob:')) return url;
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (VIDEO_REDIRECT_HOSTS.some((suffix) => host === suffix || host.endsWith(`.${suffix}`))) {
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
    const primary = proxiedVideoUrl(rawUrl);
    const fallback = redirectedVideoUrl(rawUrl);
    push(primary);
    push(fallback);
  }
  return candidates;
}

export function platformExternalUrl(rawUrl: unknown): string {
  const url = String(rawUrl || '').trim();
  return /^https?:\/\//i.test(url) ? url : '';
}
