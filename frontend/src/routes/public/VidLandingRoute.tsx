import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ShopByViltroxCard } from "../../components/public/ShopByViltroxCard";
import { ViaVideoShowcase } from "../../components/public/ViaVideoShowcase";
import { fetchCreatorPublicPageData } from "../../services/student.service";
import type { CreatorPublicPageData } from "../../types/api";

export default function VidLandingRoute() {
  const { vid = "" } = useParams();
  const [pageData, setPageData] = useState<CreatorPublicPageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void fetchCreatorPublicPageData(vid)
      .then((response) => {
        if (active) setPageData(response);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Creator page unavailable");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [vid]);

  const creator = pageData?.creator;
  const creatorName = creator?.name || vid || "Viltrox Creator";
  const creatorCode = creator?.code || pageData?.vid || vid;
  const encodedVid = encodeURIComponent(creatorCode || vid);
  const viaUrl = `/vid/${encodedVid}/via`;
  const heroImage = "/mockups/viltrox-hero-lab-n-fe.jpg";
  const initials = creatorName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("") || "V";
  const totalViews = (pageData?.featuredVideos || []).reduce((sum, item) => sum + Number(item.views || 0), 0);
  const reachLabel = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(totalViews);

  async function sharePage() {
    const shareUrl = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({ title: `${creatorName} · Viltrox VIA`, url: shareUrl });
      } else {
        await navigator.clipboard.writeText(shareUrl);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1800);
      }
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="bw-app bw-app--vid creator-public-shell">
      <header className="creator-public-topbar">
        <Link className="creator-public-brand" to="/">VILTROX</Link>
        <nav aria-label="Creator public links">
          <a href="https://www.instagram.com/viltroxofficial/" target="_blank" rel="noreferrer">Instagram</a>
          <a href="https://www.youtube.com/@Viltrox" target="_blank" rel="noreferrer">YouTube</a>
          <button type="button" onClick={() => void sharePage()}>
            {copied ? "Copied" : "Share This Page ↗"}
          </button>
        </nav>
      </header>

      <main className="creator-public-page">
        {loading ? <div className="vid-empty">Loading Creator Page...</div> : null}
        {error ? <div className="vid-empty">{error}</div> : null}

        {!loading && !error && pageData && creator ? (
          <>
            <section
              className="creator-public-hero"
              style={{
                backgroundImage: `linear-gradient(90deg, rgba(0,0,0,.92) 0%, rgba(0,0,0,.72) 42%, rgba(0,0,0,.14) 100%), url("${heroImage}")`,
              }}
            >
              <div className="creator-public-hero__copy">
                <div className="creator-public-avatar">
                  {creator.avatarUrl ? <img src={creator.avatarUrl} alt="" /> : <span>{initials}</span>}
                  <em>V</em>
                </div>
                <small>Viltrox Creator</small>
                <h1>{creatorName}</h1>
                <p>Create <b>·</b> Share <b>·</b> Earn</p>
                <div className="creator-public-bio">
                  Filmmaker & Photographer<br />
                  Capturing stories with Viltrox lenses.
                </div>
                <div className="creator-public-stats" aria-label="Creator stats">
                  <span><strong>{pageData.featuredVideos.length || 0}</strong>Videos</span>
                  <span><strong>{reachLabel}</strong>Views</span>
                  <span><strong>{pageData.accounts?.length || 0}</strong>Platforms</span>
                </div>
              </div>
              <span className="creator-public-signature">{creatorName.split(" ")[0] || "Viltrox"}</span>
            </section>

            <ViaVideoShowcase creator={creator} videos={pageData.featuredVideos} viaUrl={viaUrl} />
            <ShopByViltroxCard creator={creator} shopHeroes={pageData.shopHeroes} />
          </>
        ) : null}
      </main>

      <footer className="creator-public-footer">
        <strong>VILTROX</strong>
        <span>Powered by Viltrox VIA</span>
        <a href="https://viltrox.com/" target="_blank" rel="noreferrer">Official Store</a>
      </footer>
    </div>
  );
}
