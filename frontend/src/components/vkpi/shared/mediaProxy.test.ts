import { describe, expect, it } from 'vitest';
import { proxiedImageUrl, proxiedVideoUrl, redirectedVideoUrl } from './mediaProxy';

describe('media proxy URL normalization', () => {
  it('routes the screenshot TikTok signed image host through the same-origin proxy', () => {
    const raw = 'https://p19-common-sign.tiktokcdn-us.com/tos-useast5-avt-0068-tx/avatar~tplv-tiktokx-cropcenter:720:720.jpeg?x-signature=secret';
    expect(proxiedImageUrl(raw)).toBe(`/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`);
  });

  it('does not mistake a protocol-relative CDN URL for a local application path', () => {
    const raw = '//p19-common-sign.tiktokcdn-us.com/tos-useast5-avt/avatar.jpeg?x-signature=secret';
    expect(proxiedImageUrl(raw)).toBe(
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(`https:${raw}`)}`,
    );
  });

  it('keeps real local cache paths unchanged', () => {
    expect(proxiedImageUrl('/api/vkpi-media/image-cache/deadbeef')).toBe('/api/vkpi-media/image-cache/deadbeef');
  });

  it('routes YouTube thumbnails through the same-origin proxy so transient edge 404s stay non-blocking', () => {
    const raw = 'https://i.ytimg.com/vi/snvnP6LTzEE/maxresdefault.jpg';
    expect(proxiedImageUrl(raw)).toBe(`/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`);
  });

  it.each([
    'https://yt3.ggpht.com/profile-avatar',
    'https://yt3.googleusercontent.com/profile-avatar',
  ])('routes YouTube profile avatars through the cache-building proxy: %s', (raw) => {
    expect(proxiedImageUrl(raw)).toBe(`/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`);
  });

  it('normalizes protocol-relative video URLs for proxy and redirect candidates', () => {
    const raw = '//v16-webapp-prime.tiktokcdn-us.com/video/test.mp4';
    const absolute = `https:${raw}`;
    expect(proxiedVideoUrl(raw)).toBe(`/api/admin/vkpi/media/video-proxy?url=${encodeURIComponent(absolute)}`);
    expect(redirectedVideoUrl(raw)).toBe(`/api/admin/vkpi/media/video-redirect?url=${encodeURIComponent(absolute)}`);
  });
});
