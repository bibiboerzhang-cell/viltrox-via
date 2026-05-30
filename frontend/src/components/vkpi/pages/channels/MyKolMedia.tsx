import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import type { VkpiPlatform } from '../../vkpiTypes';
import { platformDisplay } from '../../shared/vkpiDataUtils';
import { likelyVideoUrl, platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, useCachedVideoUrl } from '../../shared/mediaProxy';
import type { PostPreview } from './myKolMatrixTypes';
import { compactDate, conciseText, displayCount, renderableMediaUrl, uniqueStrings } from './myKolMatrixData';

export function kolMediaState(post: PostPreview, platform: VkpiPlatform) {
  const fallbackUrl = String(post.mediaUrl || '').trim();
  const fallbackLooksVideo = likelyVideoUrl(fallbackUrl, platform);
  const videoUrl = post.videoUrl || (fallbackLooksVideo ? fallbackUrl : '');
  const resolvedVideoUrl = videoUrl ? proxiedVideoUrl(videoUrl) : '';
  const imageCandidates = [
    ...(post.imageUrls || []),
    ...(post.mediaUrls || []).filter((url) => !likelyVideoUrl(url, platform)),
    ...(post.imageUrl ? [post.imageUrl] : []),
    ...(fallbackUrl && !fallbackLooksVideo ? [fallbackUrl] : []),
  ];
  const imageUrls = uniqueStrings(imageCandidates.map((url) => proxiedImageUrl(url)).filter((url) => renderableMediaUrl(url, platform)));
  const videoRenderable = renderableMediaUrl(resolvedVideoUrl, platform);
  const explicitKind = `${post.contentType}`.toLowerCase();
  const kind = videoRenderable
    ? 'video'
    : (imageUrls.length > 1 || explicitKind.includes('carousel') || explicitKind.includes('sidecar') ? 'carousel' : imageUrls.length ? 'image' : 'pending');
  return {
    kind,
    videoUrl: videoRenderable ? resolvedVideoUrl : '',
    imageUrls,
    renderable: videoRenderable || imageUrls.length > 0,
  };
}

export function mediaBadge(post: PostPreview, platform: VkpiPlatform) {
  const media = kolMediaState(post, platform);
  if (media.kind === 'video') return 'video';
  if (media.kind === 'carousel') return media.imageUrls.length > 1 ? `1/${media.imageUrls.length}` : 'carousel';
  const type = `${post.contentType} ${post.rawText}`.toLowerCase();
  if (type.includes('video') || type.includes('reel')) return 'video';
  if (type.includes('carousel') || type.includes('sidecar')) return 'carousel';
  if (media.kind === 'image') return 'image';
  return '待缓存';
}

function kolVideoLookupId(post: PostPreview) {
  return String(post.snapshotId || post.id || post.url || '').trim();
}

function rawVideoCandidate(post: PostPreview, platform: VkpiPlatform) {
  return post.videoUrl
    || post.mediaUrls.find((url) => likelyVideoUrl(url, platform))
    || (likelyVideoUrl(post.mediaUrl, platform) ? post.mediaUrl : '');
}

export function KolMediaSlot({
  post,
  platform,
  compact = false,
  apiToken,
}: {
  post: PostPreview;
  platform: VkpiPlatform;
  compact?: boolean;
  apiToken?: string;
}) {
  const [active, setActive] = useState(0);
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setActive(0);
    setFailedImages(new Set());
  }, [post.id, post.mediaUrl, post.videoUrl, post.imageUrls.join('|'), post.mediaUrls.join('|')]);

  const media = kolMediaState(post, platform);
  const cachedVideoUrl = useCachedVideoUrl(apiToken, platform, kolVideoLookupId(post), rawVideoCandidate(post, platform));
  const videoUrl = cachedVideoUrl || media.videoUrl;
  const hasVideo = Boolean(videoUrl);
  if (!media.renderable && !hasVideo) return <span className="vkpi-my-kol-content-card__pending">待缓存</span>;

  const imageUrls = media.imageUrls.filter((url) => !failedImages.has(url));
  const currentImage = imageUrls[Math.min(active, Math.max(0, imageUrls.length - 1))];
  const markFailed = (url: string) => setFailedImages((prev) => {
    const next = new Set(prev);
    next.add(url);
    return next;
  });

  if (hasVideo && (!compact || !currentImage)) {
    return (
      <>
        <video
          src={videoUrl}
          poster={currentImage || imageUrls[0] || undefined}
          controls={!compact}
          muted={compact}
          playsInline
          preload="metadata"
        />
        {compact ? <span className="vkpi-my-kol-content-card__play">▶</span> : null}
      </>
    );
  }

  if (!currentImage) return <span className="vkpi-my-kol-content-card__pending">待缓存</span>;

  return (
    <div className="vkpi-my-kol-content-card__carousel">
      <img src={currentImage} alt="" loading="lazy" onError={() => markFailed(currentImage)} />
      {media.kind === 'video' ? <span className="vkpi-my-kol-content-card__play">▶</span> : null}
      {imageUrls.length > 1 ? (
        <>
          <button type="button" className="is-prev" onClick={(event) => { event.stopPropagation(); setActive((value) => (value + imageUrls.length - 1) % imageUrls.length); }} aria-label="上一张">‹</button>
          <button type="button" className="is-next" onClick={(event) => { event.stopPropagation(); setActive((value) => (value + 1) % imageUrls.length); }} aria-label="下一张">›</button>
          <span>{Math.min(active + 1, imageUrls.length)}/{imageUrls.length}</span>
        </>
      ) : null}
    </div>
  );
}

export function KolMediaLightbox({
  post,
  platform,
  apiToken,
  onClose,
}: {
  post: PostPreview;
  platform: VkpiPlatform;
  apiToken?: string;
  onClose: () => void;
}) {
  const [active, setActive] = useState(0);
  const media = kolMediaState(post, platform);
  const cachedVideoUrl = useCachedVideoUrl(apiToken, platform, kolVideoLookupId(post), rawVideoCandidate(post, platform));
  const videoUrl = cachedVideoUrl || media.videoUrl;
  const isVideo = Boolean(videoUrl);

  useEffect(() => {
    setActive(0);
  }, [post.id]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft' && media.imageUrls.length > 1) setActive((value) => (value + media.imageUrls.length - 1) % media.imageUrls.length);
      if (event.key === 'ArrowRight' && media.imageUrls.length > 1) setActive((value) => (value + 1) % media.imageUrls.length);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [media.imageUrls.length, onClose]);

  const currentImage = media.imageUrls[Math.min(active, Math.max(0, media.imageUrls.length - 1))];
  const externalUrl = platformExternalUrl(post.url);

  return createPortal(
    <div className="vkpi-media-lightbox vkpi-kol-media-lightbox" role="dialog" aria-modal="true" aria-label="KOL内容媒体预览" onClick={onClose}>
      <div className="vkpi-media-lightbox__panel" onClick={(event) => event.stopPropagation()}>
        <header className="vkpi-media-lightbox__header">
          <div>
            <span>{platformDisplay(platform)} 本地预览</span>
            <h3 title={post.title}>{conciseText(post.title, 96)}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className={`vkpi-media-lightbox__stage ${isVideo ? 'is-video' : 'is-image'}`}>
          {isVideo ? (
            <video src={videoUrl} poster={media.imageUrls[0] || undefined} controls playsInline autoPlay />
          ) : currentImage ? (
            <>
              <img src={currentImage} alt="" />
              {media.imageUrls.length > 1 ? (
                <>
                  <button type="button" className="is-prev" onClick={() => setActive((value) => (value + media.imageUrls.length - 1) % media.imageUrls.length)} aria-label="上一张">‹</button>
                  <button type="button" className="is-next" onClick={() => setActive((value) => (value + 1) % media.imageUrls.length)} aria-label="下一张">›</button>
                  <span>{active + 1}/{media.imageUrls.length}</span>
                </>
              ) : null}
            </>
          ) : (
            <div className="vkpi-media-lightbox__pending">当前帖子还没有缓存可播放媒体</div>
          )}
        </div>
        <footer className="vkpi-media-lightbox__footer">
          <span>播放 {displayCount(post.views)}</span>
          <span>点赞 {displayCount(post.likes)}</span>
          <span>评论 {displayCount(post.comments)}</span>
          <span>分享 {displayCount(post.shares)}</span>
          {externalUrl ? <a href={externalUrl} target="_blank" rel="noreferrer">打开原帖</a> : null}
          <small>{media.renderable ? '本地媒体代理' : '只保留原帖链接'} · {compactDate(post.publishedAt)}</small>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
