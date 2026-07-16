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
    render(<Avatar name="Alpha Creator" src="https://scontent-ord5-1.cdninstagram.com/v/expired.jpg" size="xs" />);
    const image = screen.getByAltText('Alpha Creator') as HTMLImageElement;
    Object.defineProperty(image, 'naturalWidth', { configurable: true, value: 1 });
    Object.defineProperty(image, 'naturalHeight', { configurable: true, value: 1 });
    fireEvent.load(image);

    expect(screen.getByLabelText('Alpha Creator').textContent).toBe('AC');
  });

  it('retries when the source changes after a failed avatar', async () => {
    const { rerender } = render(<Avatar name="Alpha Creator" src="https://scontent-ord5-1.cdninstagram.com/v/old.jpg" size="xs" />);
    fireEvent.error(screen.getByAltText('Alpha Creator'));
    expect(screen.getByLabelText('Alpha Creator').textContent).toBe('AC');

    const next = 'https://scontent-ord5-1.cdninstagram.com/v/new.jpg';
    rerender(<Avatar name="Alpha Creator" src={next} size="xs" />);
    await waitFor(() => {
      expect(screen.getByAltText('Alpha Creator').getAttribute('src')).toBe(
        `/api/admin/vkpi/media/image-proxy?url=${encodeURIComponent(next)}`,
      );
    });
  });
});
