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

  it('offers keyframe QA only for ready YouTube final_v1 and distinguishes queued from reviewed', () => {
    const onQa = vi.fn();
    const task = (status: string, dataStatus = 'none') => ({
      status, job_id: status === 'not_requested' ? null : 9, requested_at: null, updated_at: null,
      data: { status: dataStatus, freshness: dataStatus === 'ready' ? 'fresh' : 'never', updated_at: null, superseded_by_job: false },
    });
    const youtube = {
      ...video(), platform: 'youtube', content_url: 'https://youtu.be/abc', has_final_v1_cache: true,
      tasks: { metric_refresh: task('not_requested'), final_v1: task('ready', 'ready'), keyframe_qa: task('not_requested') },
    } as VkpiKolPoolVideoRow;
    const { getByRole, rerender, queryByRole, getByText } = render(
      <KolVideoSection videos={[youtube]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={onQa} />,
    );
    fireEvent.click(getByRole('button', { name: '关键帧复核' }));
    expect(onQa).toHaveBeenCalledWith(youtube);

    const queued = { ...youtube, tasks: { ...youtube.tasks!, keyframe_qa: task('queued') } };
    rerender(<KolVideoSection videos={[queued]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={onQa} />);
    expect(getByRole('button', { name: '复核已排队' })).toBeDisabled();

    const failed = { ...youtube, has_keyframe_qa_cache: true, tasks: { ...youtube.tasks!, keyframe_qa: task('failed') } };
    rerender(<KolVideoSection videos={[failed]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={onQa} />);
    expect(getByRole('button', { name: '重新复核' })).toBeEnabled();

    const reviewed = { ...youtube, tasks: { ...youtube.tasks!, keyframe_qa: task('ready', 'ready') } };
    rerender(<KolVideoSection videos={[reviewed]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={onQa} />);
    expect(getByText('关键帧已复核')).toBeInTheDocument();
    expect(queryByRole('button', { name: '关键帧复核' })).toBeNull();
  });

  it('does not expose keyframe QA for a non-YouTube row or a row without final_v1', () => {
    const { queryByRole, rerender } = render(
      <KolVideoSection videos={[{ ...video(), has_final_v1_cache: true }]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={vi.fn()} />,
    );
    expect(queryByRole('button', { name: '关键帧复核' })).toBeNull();
    rerender(<KolVideoSection videos={[{ ...video(), platform: 'youtube', content_url: 'https://youtube.com/watch?v=x' }]} queuedEvidence={new Set()} busyKeys={new Set()} onEnqueueOne={vi.fn()} onEnqueueKeyframeQa={vi.fn()} />);
    expect(queryByRole('button', { name: '关键帧复核' })).toBeNull();
  });
});
