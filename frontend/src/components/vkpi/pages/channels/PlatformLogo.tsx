function platformTone(platform: string) {
  const key = platform.toLowerCase();
  if (key === 'instagram') return 'ig';
  if (key === 'tiktok') return 'tt';
  if (key === 'youtube') return 'yt';
  if (key === 'facebook') return 'fb';
  if (key === 'reddit') return 'rd';
  if (key === 'x') return 'x';
  return 'other';
}

export function PlatformLogo({ platform, label, size = 'medium' }: { platform: string; label: string; size?: 'medium' | 'small' }) {
  const key = platform.toLowerCase();
  const className = `vkpi-platform-logo vkpi-platform-logo--${platformTone(platform)}${size === 'small' ? ' vkpi-platform-logo--small' : ''}`;
  if (key === 'instagram') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="5.1" y="5.1" width="13.8" height="13.8" rx="4.2" />
          <circle cx="12" cy="12" r="3.2" />
          <circle cx="16.2" cy="7.8" r="1" />
        </svg>
      </span>
    );
  }
  if (key === 'youtube') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9.4 7.7v8.6L16.8 12 9.4 7.7Z" />
        </svg>
      </span>
    );
  }
  if (key === 'tiktok') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M14.7 4.2c.3 2.5 1.7 4 4.1 4.4v3.2a7 7 0 0 1-4.1-1.3v5.3a5.1 5.1 0 1 1-5.1-5.1c.4 0 .8 0 1.2.1v3.5a1.9 1.9 0 1 0 1.3 1.8V4.2h2.6Z" />
        </svg>
      </span>
    );
  }
  if (key === 'reddit') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M15.8 4.6l-2.3-.5-1 4.2" />
          <circle cx="17.7" cy="5" r="1.4" />
          <path d="M7.1 10.8a2 2 0 0 0-3.3 1.5c0 .9.6 1.6 1.4 1.9a5.8 5.8 0 0 0-.1 1.1c0 3.1 3.1 5.6 6.9 5.6s6.9-2.5 6.9-5.6c0-.4 0-.7-.1-1.1a2 2 0 1 0-1.9-3.4 8.8 8.8 0 0 0-4.9-1.4 8.8 8.8 0 0 0-4.9 1.4Z" />
          <circle cx="9.3" cy="14.7" r="1" className="vkpi-platform-logo__cutout" />
          <circle cx="14.7" cy="14.7" r="1" className="vkpi-platform-logo__cutout" />
          <path d="M9.5 17.2c1.4 1 3.6 1 5 0" className="vkpi-platform-logo__stroke" />
        </svg>
      </span>
    );
  }
  if (key === 'facebook') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M14 8.1h2.3V4.4c-.4 0-1.7-.2-3.2-.2-3.2 0-5.4 2-5.4 5.6V13H4.2v4.1h3.5V23h4.4v-5.9h3.4l.6-4.1h-4V10c0-1.2.4-1.9 1.9-1.9Z" />
        </svg>
      </span>
    );
  }
  if (key === 'x') {
    return (
      <span className={className} aria-label={label}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M14.4 10.4 21.2 3h-1.6l-5.9 6.4L9 3H3.6l7.2 9.7L3.6 21h1.6l6.3-7.2 5.1 7.2H22l-7.6-10.6Zm-2.2 2.5-.7-1L5.7 4.2h2.5l4.7 6.3.7 1 6.1 8.2h-2.5l-5-6.8Z" />
        </svg>
      </span>
    );
  }
  return <span className={className}>{label.slice(0, 1).toUpperCase()}</span>;
}
