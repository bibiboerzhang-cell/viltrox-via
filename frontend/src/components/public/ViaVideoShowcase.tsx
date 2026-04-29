import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { CreatorPublicPageData, VideoItem } from "../../types/api";

interface Props {
  creator: CreatorPublicPageData["creator"];
  videos: VideoItem[];
  viaUrl: string;
}

function videoTitle(video: VideoItem | undefined) {
  return video?.title || (video?.id ? `Via video #${video.id}` : "Creator story");
}

function durationLabel(index: number) {
  const presets = ["02:48", "01:36", "00:59", "01:22", "01:18"];
  return presets[index % presets.length];
}

export function ViaVideoShowcase({ creator, videos, viaUrl }: Props) {
  const visibleVideos = useMemo(() => videos.slice(0, 5), [videos]);
  const [activeIndex, setActiveIndex] = useState(0);
  const activeVideo = visibleVideos[activeIndex] || visibleVideos[0];

  useEffect(() => {
    if (visibleVideos.length <= 1) return undefined;
    const timer = window.setInterval(() => {
      setActiveIndex((current) => (current + 1) % visibleVideos.length);
    }, 6000);
    return () => window.clearInterval(timer);
  }, [visibleVideos.length]);

  return (
    <section className="creator-via-showcase" aria-label={`${creator.name} Via Creator Space`}>
      <div className="creator-via-showcase__copy">
        <small>Via</small>
        <h2>Creator Space</h2>
        <p>Explore my latest videos and stories.</p>
        <Link className="creator-via-showcase__cta" to={viaUrl}>
          ▶ Explore Videos
        </Link>
      </div>

      <div className="creator-via-showcase__main">
        {activeVideo?.mediaUrl ? (
          <video
            key={activeVideo.id}
            src={activeVideo.mediaUrl}
            poster={activeVideo.posterUrl}
            muted
            playsInline
            preload="metadata"
          />
        ) : (
          <div
            className="creator-via-showcase__poster"
            style={activeVideo?.posterUrl ? { backgroundImage: `url("${activeVideo.posterUrl}")` } : undefined}
          >
            <span>▶</span>
          </div>
        )}
        <button className="creator-via-showcase__play" type="button" onClick={() => setActiveIndex(activeIndex)}>
          ▶
        </button>
        <strong>{videoTitle(activeVideo)}</strong>
        <em>{durationLabel(activeIndex)}</em>
      </div>

      <div className="creator-via-showcase__thumbs" aria-label="Featured videos">
        {visibleVideos.length ? visibleVideos.slice(1, 5).map((video, index) => {
          const actualIndex = index + 1;
          return (
            <button
              key={video.id}
              type="button"
              className={actualIndex === activeIndex ? "is-active" : ""}
              onClick={() => setActiveIndex(actualIndex)}
            >
              {video.mediaUrl ? (
                <video src={video.mediaUrl} poster={video.posterUrl} muted playsInline preload="metadata" />
              ) : (
                <span
                  className="creator-via-showcase__thumb-poster"
                  style={video.posterUrl ? { backgroundImage: `url("${video.posterUrl}")` } : undefined}
                />
              )}
              <strong>▶</strong>
              <em>{durationLabel(actualIndex)}</em>
            </button>
          );
        }) : (
          <div className="creator-via-showcase__empty">
            Featured Via videos will appear here after admin approval.
          </div>
        )}
      </div>

      {visibleVideos.length > 1 ? (
        <div className="creator-via-showcase__dots" aria-label="Via video carousel">
          {visibleVideos.map((video, index) => (
            <button
              key={video.id}
              type="button"
              className={index === activeIndex ? "is-active" : ""}
              aria-label={`Show Via video ${index + 1}`}
              onClick={() => setActiveIndex(index)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
