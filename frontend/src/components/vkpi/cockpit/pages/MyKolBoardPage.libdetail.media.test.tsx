import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { VkpiKolPoolVideoRow } from '../../../../services/vkpi/myKolBoard-api';
import { KolVideoSection } from './MyKolBoardPage.libdetail';

const rawThumbnail = '//p19-common-sign.tiktokcdn-us.com/tos-useast5-avt/avatar.jpeg?x-signature=secret';

function video(): VkpiKolPoolVideoRow {
  return {
    evidence_id: 42,
    title: 'TikTok signed thumbnail',
    thumbnail_url: rawThumbnail,
    content_url: 'https://www.tiktok.com/@sample/video/42',
    publish_date: '2026-07-13',
    tracking_status: 'tracked',
    freshness: 'fresh',
    views_delta_24h: 25,
    views_delta_7d: 140,
    delta_24h_status: 'ready',
    delta_7d_status: 'ready',
    sample_count: 4,
    attempt_count: 5,
    last_success: { status: 'success', fetched_at: '2026-08-21T12:00:00Z' },
  } as VkpiKolPoolVideoRow;
}

describe('KolVideoSection media safety', () => {
  it('never renders a signed TikTok thumbnail as a naked third-party URL', () => {
    const { container } = render(
      <KolVideoSection videos={[video()]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} />,
    );
    const image = container.querySelector('img');
    expect(image?.getAttribute('src')).toBe(
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(`https:${rawThumbnail}`)}`,
    );
  });

  it('shows the honest placeholder after the proxied image still fails', () => {
    const { container, getByTitle } = render(
      <KolVideoSection videos={[video()]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} />,
    );
    const image = container.querySelector('img');
    expect(image).not.toBeNull();
    fireEvent.error(image as HTMLImageElement);
    expect(getByTitle('TikTok signed thumbnail · 缩略图暂不可用')).toBeInTheDocument();
  });

  it('shows bounded 24h and 7d snapshot trends with the last refresh stamp', () => {
    const { getByTestId } = render(
      <KolVideoSection videos={[video()]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} />,
    );
    expect(getByTestId('video-trend-42')).toHaveTextContent('24h +25 · 7d +140 · 最后刷新 2026-08-21 12:00');
    expect(getByTestId('video-trend-42')).toHaveAttribute('title', expect.stringContaining('不是实时数据'));
  });
});
