import { useEffect, useState } from 'react';
import { proxiedImageUrl } from '../pages/data-analysis/utils/mediaProxy';

const AVATAR_STATUS_LABELS: Record<string, string> = {
  durable: '头像健康',
  external: '外部头像，等待本地缓存',
  ephemeral: '临时头像，可能过期',
  expired: '头像已过期，显示姓名缩写',
  invalid: '头像地址无效，显示姓名缩写',
  missing: '暂无头像，显示姓名缩写',
  load_failed: '头像加载失败，显示姓名缩写',
};

export function avatarStatusLabel(status: unknown): string {
  const key = String(status || '').trim().toLowerCase();
  return AVATAR_STATUS_LABELS[key] || (key ? `头像状态：${key}` : '');
}

export function Avatar({ name, src, size = 'md', status }: { name: string; src?: string; size?: 'xs' | 'sm' | 'md' | 'lg'; status?: string }) {
  const [failed, setFailed] = useState(false);
  const initials = name
    .split(' ')
    .map((part) => part.charAt(0))
    .join('')
    .slice(0, 2)
    .toUpperCase();

  const resolvedSrc = proxiedImageUrl(src);
  const normalizedStatus = String(status || '').trim().toLowerCase();
  const effectiveStatus = failed
    ? 'load_failed'
    : !resolvedSrc && ['durable', 'ephemeral'].includes(normalizedStatus)
      ? 'missing'
      : normalizedStatus || (!resolvedSrc ? 'missing' : '');
  const statusLabel = avatarStatusLabel(effectiveStatus);
  const unusableStatus = ['expired', 'invalid', 'missing', 'load_failed'].includes(effectiveStatus);

  useEffect(() => {
    setFailed(false);
  }, [resolvedSrc]);

  return resolvedSrc && !failed && !unusableStatus ? (
    <img
      className={`vkpi-avatar is-${size}`}
      src={resolvedSrc}
      alt={name}
      data-avatar-status={effectiveStatus || undefined}
      title={statusLabel || undefined}
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
    <span className={`vkpi-avatar vkpi-avatar--fallback is-${size}`} aria-label={name} data-avatar-status={effectiveStatus || 'missing'} title={statusLabel || '暂无头像，显示姓名缩写'}>{initials}</span>
  );
}
