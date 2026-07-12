import { describe, it, expect } from 'vitest';
import { mapPost, mediaState, viewsLabel, viewsUnavailableText } from './ChannelContentList.helpers';
import type { ChannelContentPost, OfficialChannelAccount } from './channelTypes';

// 官号内容层播放/缩略图诚实化冒烟(2026-07-12)三条红线:
// ①图片/轮播帖没有播放量口径 → 显 —,绝不显 0 撒谎
// ②视频帖 0(实测零)与 NULL(未采集,views_missing)区分
// ③累计口径:— 不混进合计(合计端用 viewsUnavailable||viewsMissing 过滤)

const igAccount = { platform: 'instagram', platformLabel: 'Instagram' } as OfficialChannelAccount;

function post(overrides: Partial<ChannelContentPost>): ChannelContentPost {
  return mapPost({ id: 'p1', title: 't', url: 'https://instagram.com/p/x', ...overrides });
}

describe('viewsLabel 播放数诚实口径', () => {
  it('图文帖 views_unavailable → 显 — 而非 0', () => {
    const item = post({ media_kind: 'image', views: 0, views_unavailable: true });
    expect(item.viewsUnavailable).toBe(true);
    expect(viewsLabel(item)).toBe('—');
  });

  it('轮播帖 snake_case views_unavailable 也吃得进', () => {
    const item = post({ media_kind: 'carousel', views: 0, views_unavailable: true });
    expect(viewsLabel(item)).toBe('—');
  });

  it('视频帖播放 0 与 NULL 区分:0=实测零显 0,NULL(views_missing)显 —', () => {
    const measuredZero = post({ media_kind: 'video', views: 0 });
    const neverCollected = post({ media_kind: 'video', views: 0, views_missing: true });
    expect(viewsLabel(measuredZero)).toBe('0');
    expect(measuredZero.viewsMissing).toBe(false);
    expect(neverCollected.viewsMissing).toBe(true);
    expect(viewsLabel(neverCollected)).toBe('—');
  });

  it('视频帖有真实播放数 → 正常显示数字', () => {
    const item = post({ media_kind: 'video', views: 5114 });
    expect(viewsLabel(item)).toBe('5.1K');
  });
});

describe('viewsUnavailableText 口径说明', () => {
  it('图文帖给「无播放量口径」说明', () => {
    const item = post({ media_kind: 'image', views: 0, views_unavailable: true });
    expect(viewsUnavailableText(item, igAccount)).toContain('图文帖无播放量口径');
  });

  it('未采集(views_missing)给「未采集」说明,与实测 0 区分', () => {
    const item = post({ media_kind: 'video', views: 0, views_missing: true });
    expect(viewsUnavailableText(item, igAccount)).toContain('未采集');
  });

  it('后端下发的 reason 优先', () => {
    const item = post({ views_unavailable: true, views_unavailable_reason: 'IG 图文/轮播没有公开视频播放量，需要后台 Insights 才能补齐。' });
    expect(viewsUnavailableText(item, igAccount)).toContain('Insights');
  });
});

describe('mediaState 缩略图链', () => {
  it('本地缓存 URL(/api/vkpi-media/image-cache/…)判定为可渲染', () => {
    const item = post({ media_kind: 'image', image_urls: ['/api/vkpi-media/image-cache/abc'] });
    const media = mediaState(item, igAccount);
    expect(media.renderable).toBe(true);
    expect(media.imageUrls[0]).toBe('/api/vkpi-media/image-cache/abc');
  });

  it('IG CDN 直链走媒体代理端点(防盗链),不裸放 scontent 直链', () => {
    const item = post({ media_kind: 'image', image_urls: ['https://scontent-lga3-3.cdninstagram.com/v/pic.jpg'] });
    const media = mediaState(item, igAccount);
    expect(media.imageUrls[0]).toContain('/api/admin/vkpi/media/image-proxy?url=');
  });

  it('无任何媒体候选 → renderable=false(诚实占位,不造假图)', () => {
    const item = post({ media_kind: 'image' });
    const media = mediaState(item, igAccount);
    expect(media.renderable).toBe(false);
    expect(media.imageUrls).toHaveLength(0);
  });
});
