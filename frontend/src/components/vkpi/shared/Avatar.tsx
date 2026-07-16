import { useEffect, useState } from 'react';
import { proxiedImageUrl } from '../pages/data-analysis/utils/mediaProxy';

export function Avatar({ name, src, size = 'md' }: { name: string; src?: string; size?: 'xs' | 'sm' | 'md' | 'lg' }) {
  const [failed, setFailed] = useState(false);
  const initials = name
    .split(' ')
    .map((part) => part.charAt(0))
    .join('')
    .slice(0, 2)
    .toUpperCase();

  const resolvedSrc = proxiedImageUrl(src);

  useEffect(() => {
    setFailed(false);
  }, [resolvedSrc]);

  return resolvedSrc && !failed ? (
    <img
      className={`vkpi-avatar is-${size}`}
      src={resolvedSrc}
      alt={name}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      onLoad={(event) => {
        const { naturalHeight, naturalWidth } = event.currentTarget;
        if (naturalWidth > 0 && naturalHeight > 0 && naturalWidth <= 2 && naturalHeight <= 2) {
          setFailed(true);
        }
      }}
    />
  ) : (
    <span className={`vkpi-avatar vkpi-avatar--fallback is-${size}`} aria-label={name}>{initials}</span>
  );
}
