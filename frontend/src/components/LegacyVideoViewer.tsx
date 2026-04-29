import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { LegacyVideoViewerData } from "../lib/legacyVideo";

function viewerRatio(width: number, height: number): "portrait" | "landscape" | "square" {
  if (!width || !height) {
    return "landscape";
  }
  const ratio = width / height;
  if (ratio < 0.8) {
    return "portrait";
  }
  if (ratio > 1.2) {
    return "landscape";
  }
  return "square";
}

export function LegacyVideoViewer({
  open,
  data,
  onClose,
}: {
  open: boolean;
  data: LegacyVideoViewerData | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const mainVideoRef = useRef<HTMLVideoElement | null>(null);
  const backdropVideoRef = useRef<HTMLVideoElement | null>(null);
  const [ratio, setRatio] = useState<"portrait" | "landscape" | "square">("landscape");
  const [showPoster, setShowPoster] = useState(true);

  useEffect(() => {
    if (!open) {
      if (mainVideoRef.current) {
        mainVideoRef.current.pause();
      }
      if (backdropVideoRef.current) {
        backdropVideoRef.current.pause();
      }
      return undefined;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  useEffect(() => {
    if (!open || !data?.uploadedVideoUrl) {
      setRatio("landscape");
      setShowPoster(true);
      return;
    }
    const video = mainVideoRef.current;
    const backdrop = backdropVideoRef.current;
    if (!video || !backdrop) {
      return;
    }
    const play = async () => {
      try {
        await video.play();
      } catch {
        // Main viewer playback is optional; controls remain available.
      }
      try {
        backdrop.muted = true;
        await backdrop.play();
      } catch {
        // Backdrop playback is decorative.
      }
    };
    void play();
  }, [data?.uploadedVideoUrl, open]);

  useEffect(() => {
    setShowPoster(true);
  }, [data?.posterUrl, data?.uploadedVideoUrl, open]);

  const shellClassName = useMemo(() => {
    return `rank-view-shell is-${ratio}`;
  }, [ratio]);

  if (!open || !data) {
    return null;
  }

  return (
    <div className="rank-view-overlay" aria-hidden={!open} onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className={shellClassName}>
        <button type="button" className="rank-view-close" aria-label={t("viewer.close")} onClick={onClose}>
          ✕
        </button>
        <div className="rank-view-stage">
          <div className="rank-view-video-backdrop">
            {data.uploadedVideoUrl ? (
              <video ref={backdropVideoRef} muted playsInline preload="metadata" src={data.uploadedVideoUrl} />
            ) : null}
          </div>
          <div className="rank-view-video-wrap">
            {data.posterUrl && showPoster ? <img src={data.posterUrl} className="rank-view-poster" alt="Poster" /> : null}
            {data.uploadedVideoUrl ? (
              <video
                ref={mainVideoRef}
                className="rank-view-video"
                controls
                playsInline
                preload="metadata"
                src={data.uploadedVideoUrl}
                onLoadedMetadata={(event) => {
                  setRatio(viewerRatio(event.currentTarget.videoWidth, event.currentTarget.videoHeight));
                }}
                onCanPlay={() => setShowPoster(false)}
                onPlay={() => setShowPoster(false)}
                onError={() => setShowPoster(Boolean(data.posterUrl))}
              />
            ) : null}
          </div>
        </div>

        <aside className="rank-view-info">
          <div className="rank-view-meta-top">
            <div className="rank-view-rank-badge">{data.badge}</div>
            <div>
              <h3 className="rank-view-handle">{data.title}</h3>
              <p className="rank-view-code">{data.subtitle}</p>
            </div>
          </div>

          <div className="rank-view-stats">
            {data.stats.map((item) => (
              <div key={`${item.label}-${item.value}`} className="rank-view-stat">
                <span className="label">{item.label}</span>
                <span className={`value ${item.valueClassName || ""}`.trim()}>{item.value}</span>
              </div>
            ))}
          </div>

          <div className="rank-view-links-block">
            <div className="rank-view-links-title">{data.mode === "rank" ? t("viewer.externalLinks") : t("viewer.sourceLinks")}</div>
            <div className="rank-view-links">
              {data.externalLinks.length ? (
                data.externalLinks.map((link) => (
                  <a key={`${link.label}-${link.url}`} href={link.url} target="_blank" rel="noreferrer">
                    {link.label || t("viewer.openLink")}
                  </a>
                ))
              ) : (
                <div className="rank-view-empty">
                  <div className="rank-view-empty-title">{t("viewer.noExternalLinksTitle")}</div>
                  <div className="rank-view-empty-text">{t("viewer.noExternalLinksBody")}</div>
                </div>
              )}
            </div>
          </div>

          {data.extraBody ? (
            <div className="rank-view-empty">
              <div className="rank-view-empty-title">{data.extraTitle || t("viewer.note")}</div>
              <div className="rank-view-empty-text">{data.extraBody}</div>
            </div>
          ) : null}

          {!data.uploadedVideoUrl ? (
            <div className="rank-view-empty">
              <div className="rank-view-empty-title">{t("viewer.noVideoTitle")}</div>
              <div className="rank-view-empty-text">{t("viewer.noVideoBody")}</div>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
