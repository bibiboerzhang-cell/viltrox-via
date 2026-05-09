import { useState } from 'react';

export function Avatar({ name, src, size = 'md' }: { name: string; src?: string; size?: 'xs' | 'sm' | 'md' | 'lg' }) {
  const [failed, setFailed] = useState(false);
  const initials = name
    .split(' ')
    .map((part) => part.charAt(0))
    .join('')
    .slice(0, 2)
    .toUpperCase();

  return src && !failed ? (
    <img className={`vkpi-avatar is-${size}`} src={src} alt={name} onError={() => setFailed(true)} />
  ) : (
    <span className={`vkpi-avatar vkpi-avatar--fallback is-${size}`} aria-label={name}>{initials}</span>
  );
}
