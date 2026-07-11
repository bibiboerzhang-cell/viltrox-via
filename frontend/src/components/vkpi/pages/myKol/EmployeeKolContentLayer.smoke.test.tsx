import { describe, it, expect, vi } from 'vitest';
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { VkpiKolOption } from '../../vkpiTypes';
import type { PostPreview } from '../channels/myKolMatrixTypes';

// 评论明细弹窗冒烟(2026-07-11):
// ①pool 路径评论读取失败必须在弹窗里可见(404=evidence 归属校验失败不静默)+ 有重试按钮
// ②sentiment='unknown' 不渲染英文字面量;language 有值时显示语言标签

// 主 kol comments 接口(本测试走 commentsFetcher 旁路,不应被调用)。
vi.mock('../../../../domains/channels', () => ({
  getKolComments: vi.fn().mockResolvedValue({ items: [], page: { total: 0, next_offset: 0 } }),
}));

// 媒体槽涉及视频缓存代理,与评论链无关,桩成轻量节点。
vi.mock('../channels/MyKolMedia', () => ({
  KolMediaSlot: () => React.createElement('span', null, 'media'),
  KolMediaLightbox: () => null,
  mediaBadge: () => 'video',
}));

import { EmployeeKolContentLayer } from './EmployeeKolContentLayer';

const kol: VkpiKolOption = {
  id: 'kol-1',
  name: '测试KOL',
  handle: '@test',
  platform: 'YouTube',
  profileUrl: 'https://youtube.com/@test',
} as VkpiKolOption;

const POST_URL = 'https://youtube.com/watch?v=abc123def45';

const post: PostPreview = {
  id: 'evidence:1',
  snapshotId: '',
  title: '测试视频',
  url: POST_URL,
  mediaUrl: '',
  videoUrl: '',
  imageUrl: '',
  imageUrls: [],
  mediaUrls: [],
  views: 100,
  likes: 10,
  comments: 3,
  shares: 1,
  publishedAt: '2026-07-01T00:00:00Z',
  contentType: 'video',
  brandMentions: ['viltrox'],
  competitorMentions: [],
  gearMentions: [],
  rawText: '测试视频',
};

function renderLayer(commentsFetcher: (p: PostPreview) => Promise<{ rows: Array<Record<string, unknown>>; total: number }>) {
  return render(
    React.createElement(EmployeeKolContentLayer, {
      apiToken: 't',
      kol,
      projects: [],
      viltroxOnly: false,
      postsOverride: [post],
      commentsFetcher,
    }),
  );
}

describe('EmployeeKolContentLayer 评论明细弹窗', () => {
  it('pool 路径评论读取失败在弹窗里可见,且有重试按钮', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('evidence not found for this kol (404)'));
    renderLayer(fetcher);

    fireEvent.click(screen.getByText('评论明细'));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    // 404 归属校验失败必须如实显示(quietMissingError 的静默只留给主 kol comments 接口)。
    const alert = await screen.findByText(/评论读取失败：evidence not found for this kol/);
    expect(alert).toBeTruthy();
    const retry = screen.getByRole('button', { name: '重试' });
    expect(retry).toBeTruthy();

    // 点重试再拉一次。
    fireEvent.click(retry);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("sentiment='unknown' 不渲染英文字面量;language 有值时显示语言标签", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      rows: [
        {
          id: 11,
          post_url: POST_URL,
          comment_text: 'Great lens!',
          author_handle: 'bob',
          like_count: 2,
          language_detected: 'en',
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      total: 1,
    });
    renderLayer(fetcher);

    fireEvent.click(screen.getByText('评论明细'));
    await screen.findByText('Great lens!');

    // 元信息行:不出现 'unknown' 占位;语言标签 en 顶上。
    expect(screen.queryByText(/unknown/)).toBeNull();
    expect(screen.getByText(/^en · /)).toBeTruthy();
  });

  it('language 也拿不到时元信息行只剩时间,不显示占位词', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      rows: [
        {
          id: 12,
          post_url: POST_URL,
          comment_text: 'No meta comment',
          author_handle: 'carol',
          like_count: 0,
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      total: 1,
    });
    renderLayer(fetcher);

    fireEvent.click(screen.getByText('评论明细'));
    await screen.findByText('No meta comment');
    expect(screen.queryByText(/unknown/)).toBeNull();
  });
});
