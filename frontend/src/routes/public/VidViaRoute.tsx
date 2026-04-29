import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fetchPublicVidProfile } from "../../services/student.service";
import type { PublicVidProfileResponse } from "../../types/api";

type PublicSubmission = NonNullable<PublicVidProfileResponse["submissions"]>[number];

function platformLabel(platform: string | undefined) {
  const value = String(platform || "").trim().toLowerCase();
  if (value === "instagram") return "Instagram";
  if (value === "youtube") return "YouTube";
  if (value === "tiktok") return "TikTok";
  if (!value) return "Platform";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function platformMark(platform: string | undefined) {
  const value = String(platform || "").trim().toLowerCase();
  if (value === "instagram") return "IG";
  if (value === "youtube") return "YT";
  if (value === "tiktok") return "TT";
  return platformLabel(platform).slice(0, 2);
}

function compactNumber(value: number | undefined) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value || 0));
}

function submissionTitle(submission: PublicSubmission) {
  return submission.title || `Via video #${submission.id}`;
}

export default function VidViaRoute() {
  const { vid = "" } = useParams();
  const [profile, setProfile] = useState<PublicVidProfileResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedSubmissionId, setSelectedSubmissionId] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void fetchPublicVidProfile(vid)
      .then((response) => {
        if (active) setProfile(response);
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Via page unavailable");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [vid]);

  const visibleVid = profile?.vid || vid;
  const encodedVid = visibleVid ? encodeURIComponent(visibleVid) : "";
  const isBound = Boolean(profile?.is_bound);
  const creatorName = profile?.creator?.name || visibleVid || "Viltrox creator";
  const accounts = useMemo(() => profile?.accounts?.filter((item) => item.verified && item.handle) ?? [], [profile?.accounts]);
  const submissions = profile?.submissions ?? [];
  const selectedSubmission =
    submissions.find((item) => item.id === selectedSubmissionId) ||
    submissions.find((item) => String(item.media_url || item.url || "").trim()) ||
    submissions[0];
  const registerUrl = `/?auth=register${visibleVid ? `&student_id=${encodeURIComponent(visibleVid)}` : ""}`;

  return (
    <div className="bw-app bw-app--vid">
      <header className="vid-topbar vid-topbar--via">
        <Link className="vid-brand" to="/">VILTROX</Link>
        <Link className="vid-return-link" to={encodedVid ? `/vid/${encodedVid}` : "/"}>
          Back to Shop / Via
        </Link>
      </header>
      <main className="vid-page vid-page--via">
        <section className="vid-via-hero vid-via-hero--clean">
          <small>Via</small>
          <h1>{creatorName}</h1>
          {accounts.length ? (
            <div className="vid-social-icons" aria-label="Bound accounts">
              {accounts.map((account) => (
                <a key={`${account.platform}-${account.handle}`} href={account.profile_url || "#"} target="_blank" rel="noreferrer">
                  <span aria-hidden="true">{platformMark(account.platform)}</span>
                  <strong>{account.handle}</strong>
                </a>
              ))}
            </div>
          ) : null}
        </section>

        {loading ? <div className="vid-empty">Loading Via...</div> : null}
        {error ? <div className="vid-empty">{error}</div> : null}

        {!loading && !error && !isBound ? (
          <section className="vid-via-section">
            <div className="vid-section-head">
              <div>
                <small>Claim required</small>
                <h2>这个 VID 还没有绑定</h2>
              </div>
            </div>
            <a className="vid-via-primary-link" href={registerUrl}>领取并创建 Via 页面</a>
          </section>
        ) : null}

        {!loading && !error && isBound ? (
          <section className="vid-via-section vid-via-section--videos">
            <div className="vid-section-head">
              <div>
                <small>Creator videos</small>
                <h2>Public Via</h2>
              </div>
              <strong>{submissions.length} videos</strong>
            </div>
            {submissions.length ? (
              <>
                <div className="vid-cinema-shell">
                  <div className="vid-cinema-window">
                    {selectedSubmission?.media_url ? (
                      <video
                        className="vid-cinema-player"
                        key={selectedSubmission.id}
                        src={selectedSubmission.media_url}
                        poster={selectedSubmission.poster_url}
                        controls
                        preload="metadata"
                      />
                    ) : selectedSubmission?.url ? (
                      <a className="vid-cinema-placeholder" href={selectedSubmission.url} target="_blank" rel="noreferrer">
                        <small>External video</small>
                        <strong>Open source video</strong>
                      </a>
                    ) : (
                      <div className="vid-cinema-placeholder">
                        <small>No preview</small>
                        <strong>Video file is not available locally</strong>
                      </div>
                    )}
                  </div>

                  <aside className="vid-cinema-info">
                    <small>Now playing</small>
                    <h3>{selectedSubmission ? submissionTitle(selectedSubmission) : "Via video"}</h3>
                    <div className="vid-cinema-stats">
                      <span>{platformLabel(selectedSubmission?.platform)}</span>
                      <span>{selectedSubmission?.status || "pending"}</span>
                      <span>{Number(selectedSubmission?.points || 0).toLocaleString()} pts</span>
                      <span>{compactNumber(selectedSubmission?.views)} views</span>
                    </div>
                    {selectedSubmission?.product_label ? <p>{selectedSubmission.product_label}</p> : null}
                    {selectedSubmission?.url ? (
                      <a className="vid-cinema-source" href={selectedSubmission.url} target="_blank" rel="noreferrer">
                        Open original
                      </a>
                    ) : null}
                  </aside>
                </div>

                <div className="vid-playlist-strip" aria-label="Via video playlist">
                  {submissions.map((submission, index) => {
                    const hasPlayableMedia = Boolean(String(submission.media_url || submission.url || "").trim());
                    const isSelected = selectedSubmission?.id === submission.id;
                    return (
                      <button
                        key={submission.id}
                        className={`vid-playlist-card${isSelected ? " is-active" : ""}`}
                        type="button"
                        onClick={() => setSelectedSubmissionId(submission.id)}
                      >
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <strong>{submissionTitle(submission)}</strong>
                        <em>{hasPlayableMedia ? "Playable" : "Metadata only"} · {Number(submission.points || 0).toLocaleString()} pts</em>
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              <div className="vid-empty">这个 Via 还没有公开视频。</div>
            )}
          </section>
        ) : null}
      </main>
    </div>
  );
}
