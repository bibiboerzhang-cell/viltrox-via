import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Avatar } from './Avatar';

describe('Avatar', () => {
  it('routes Instagram CDN avatars through the authenticated same-origin proxy', () => {
    const raw = 'https://scontent-ord5-1.cdninstagram.com/v/avatar.jpg';
    render(<Avatar name="Alpha Creator" src={raw} size="xs" />);

    expect(screen.getByAltText('Alpha Creator').getAttribute('src')).toBe(
      `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(raw)}`,
    );
    expect(document.querySelector(`img[src="${raw}"]`)).toBeNull();
  });

  it('shows honest initials when the proxy returns its 1x1 unavailable placeholder', () => {
    render(<Avatar name="Alpha Creator" src="https://scontent-ord5-1.cdninstagram.com/v/expired.jpg" status="durable" size="xs" />);
    const image = screen.getByAltText('Alpha Creator') as HTMLImageElement;
    Object.defineProperty(image, 'naturalWidth', { configurable: true, value: 1 });
    Object.defineProperty(image, 'naturalHeight', { configurable: true, value: 1 });
    fireEvent.load(image);

    const fallback = screen.getByLabelText('Alpha Creator');
    expect(fallback.textContent).toBe('AC');
    expect(fallback).toHaveAttribute('data-avatar-status', 'load_failed');
    expect(fallback).toHaveAttribute('title', '头像加载失败，显示姓名缩写');
  });

  it('exposes the server avatar health state on the honest fallback', () => {
    render(<Avatar name="Alpha Creator" src="" status="expired" size="xs" />);

    const fallback = screen.getByLabelText('Alpha Creator');
    expect(fallback).toHaveAttribute('data-avatar-status', 'expired');
    expect(fallback).toHaveAttribute('title', '头像已过期，显示姓名缩写');
    expect(fallback.textContent).toBe('AC');
  });

  it('never renders a non-empty URL that the server marks unusable', () => {
    render(<Avatar name="Alpha Creator" src="https://example.com/stale.jpg" status="expired" size="xs" />);

    expect(screen.queryByAltText('Alpha Creator')).toBeNull();
    const fallback = screen.getByLabelText('Alpha Creator');
    expect(fallback).toHaveAttribute('data-avatar-status', 'expired');
    expect(fallback.textContent).toBe('AC');
  });

  it('does not claim a healthy avatar when the URL is empty', () => {
    render(<Avatar name="Alpha Creator" src="" status="durable" size="xs" />);

    const fallback = screen.getByLabelText('Alpha Creator');
    expect(fallback).toHaveAttribute('data-avatar-status', 'missing');
    expect(fallback).toHaveAttribute('title', '暂无头像，显示姓名缩写');
  });

  it('retries when the source changes after a failed avatar', async () => {
    const { rerender } = render(<Avatar name="Alpha Creator" src="https://scontent-ord5-1.cdninstagram.com/v/old.jpg" size="xs" />);
    fireEvent.error(screen.getByAltText('Alpha Creator'));
    // 状态更新后的 DOM 断言一律等提交(慢 runner 竞态,同 ea20aa50)
    await waitFor(() => {
      expect(screen.getByLabelText('Alpha Creator').textContent).toBe('AC');
    });

    const next = 'https://scontent-ord5-1.cdninstagram.com/v/new.jpg';
    rerender(<Avatar name="Alpha Creator" src={next} size="xs" />);
    await waitFor(() => {
      expect(screen.getByAltText('Alpha Creator').getAttribute('src')).toBe(
        `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(next)}`,
      );
    });
  });
});
