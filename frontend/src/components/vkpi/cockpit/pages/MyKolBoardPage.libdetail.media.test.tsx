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
});
