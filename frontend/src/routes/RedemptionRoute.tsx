import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { FloatingViaCat } from "../components/catographer/FloatingViaCat";
import { LegacyVideoViewer } from "../components/LegacyVideoViewer";
import { BwTopNav, EmptyState } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import {
  apiFetch,
  jsonBody,
  type CreatorSubmission,
  type LeaderboardEntry,
  type LeaderboardResponse,
  type RewardItem,
  type RewardsResponse,
} from "../lib/api";
import { buildLeaderboardViewerData, type LegacyVideoViewerData } from "../lib/legacyVideo";
import { listCreatorAddresses, listCreatorProgram, listCreatorSubmissions } from "../services/creator.service";
import type { CreatorAddress } from "../types/api";
import type { CreatorProgramResponse } from "../lib/api";

type RankPeriod = "month" | "year";
type CartStep = "review" | "address";

function rewardScore(entry: LeaderboardEntry) {
  return (
    entry.points ??
    entry.total_score ??
    entry.total_campaign_score ??
    entry.total_points_earned ??
    entry.estimated_points ??
    0
  );
}

function rewardSubmissionCount(entry: LeaderboardEntry) {
  return entry.submission_count ?? entry.submissions ?? 0;
}

function isValidSubmission(submission: CreatorSubmission) {
  const status = String(submission.detection_status || "").trim().toLowerCase();
  return status === "confirmed" || status === "approved" || Number(submission.points_awarded || 0) > 0;
}

function isSubmissionInPeriod(submission: CreatorSubmission, period: RankPeriod) {
  const createdAt = String(submission.created_at || "").trim();
  if (!createdAt) {
    return false;
  }
  const date = new Date(createdAt);
  if (Number.isNaN(date.getTime())) {
    return false;
  }
  const now = new Date();
  return period === "month"
    ? date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()
    : date.getFullYear() === now.getFullYear();
}

export default function RedemptionRoute() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { status, token, user, refreshUser, openAuthModal } = useAuth();
  const [rewards, setRewards] = useState<RewardItem[]>([]);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [submissions, setSubmissions] = useState<CreatorSubmission[]>([]);
  const [program, setProgram] = useState<CreatorProgramResponse | null>(null);
  const [viewerData, setViewerData] = useState<LegacyVideoViewerData | null>(null);
  const [loadingRewards, setLoadingRewards] = useState(true);
  const [loadingLeaderboard, setLoadingLeaderboard] = useState(true);
  const [period, setPeriod] = useState<RankPeriod>("month");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("All");
  const [range, setRange] = useState("All");
  const [addresses, setAddresses] = useState<CreatorAddress[]>([]);
  const [selectedAddressId, setSelectedAddressId] = useState<number | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [cartReward, setCartReward] = useState<RewardItem | null>(null);
  const [cartStep, setCartStep] = useState<CartStep>("review");
  const [cartBusy, setCartBusy] = useState(false);
  const [cartMessage, setCartMessage] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const [surfaceMessage, setSurfaceMessage] = useState<{ tone: "success" | "warning" | "danger"; body: string } | null>(null);
  const rangeOptions = useMemo(
    () => [
      { value: "All", label: t("redeem.filterAll") },
      { value: "0–500", label: t("redeem.ranges.under500") },
      { value: "500–1000", label: t("redeem.ranges.under1000") },
      { value: "1000–3000", label: t("redeem.ranges.under3000") },
      { value: "3000+", label: t("redeem.ranges.over3000") },
    ],
    [t],
  );
  const isAuthenticated = status === "authenticated" && Boolean(user) && Boolean(token);

  function rewardImageLabel(item: RewardItem) {
    return item.meta_label || item.category || t("redeem.rewardFallbackImageLabel");
  }

  function rewardDescription(item: RewardItem) {
    return item.description || t("redeem.rewardFallbackDescription");
  }

  function rewardHandle(entry: LeaderboardEntry) {
    return entry.handle || entry.name || entry.display_name || entry.creator_code || t("redeem.creatorFallback");
  }

  function isRewardSoldOut(item: RewardItem) {
    return Number(item.stock ?? 0) <= 0;
  }

  useEffect(() => {
    let mounted = true;

    async function loadRewards() {
      setLoadingRewards(true);
      try {
        const response = await apiFetch<RewardsResponse>("/api/rewards");
        if (mounted) {
          setRewards(response.rewards || []);
        }
      } finally {
        if (mounted) {
          setLoadingRewards(false);
        }
      }
    }

    void loadRewards();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;

    async function loadLeaderboard() {
      setLoadingLeaderboard(true);
      try {
        const response = await apiFetch<LeaderboardResponse>(`/api/leaderboard?period=${period}`);
        if (mounted) {
          setLeaderboard(response.items || []);
        }
      } finally {
        if (mounted) {
          setLoadingLeaderboard(false);
        }
      }
    }

    void loadLeaderboard();
    return () => {
      mounted = false;
    };
  }, [period]);

  useEffect(() => {
    let mounted = true;

    async function loadProgram() {
      if (status !== "authenticated" || !token) {
        if (mounted) {
          setProgram(null);
        }
        return;
      }
      try {
        const nextProgram = await listCreatorProgram(token);
        if (mounted) {
          setProgram(nextProgram);
        }
      } catch {
        if (mounted) {
          setProgram(null);
        }
      }
    }

    void loadProgram();
    return () => {
      mounted = false;
    };
  }, [status, token]);

  useEffect(() => {
    let mounted = true;

    async function loadSubmissions() {
      if (status !== "authenticated" || !token) {
        if (mounted) {
          setSubmissions([]);
        }
        return;
      }
      try {
        const items = await listCreatorSubmissions(token);
        if (mounted) {
          setSubmissions(items);
        }
      } catch {
        if (mounted) {
          setSubmissions([]);
        }
      }
    }

    void loadSubmissions();
    return () => {
      mounted = false;
    };
  }, [status, token]);

  useEffect(() => {
    let mounted = true;

    async function loadAddresses() {
      if (status !== "authenticated" || !token) {
        if (mounted) {
          setAddresses([]);
          setSelectedAddressId(null);
        }
        return;
      }
      try {
        const nextAddresses = await listCreatorAddresses(token);
        if (mounted) {
          setAddresses(nextAddresses);
        }
      } catch {
        if (mounted) {
          setAddresses([]);
        }
      }
    }

    void loadAddresses();
    return () => {
      mounted = false;
    };
  }, [status, token]);

  useEffect(() => {
    if (!addresses.length) {
      setSelectedAddressId(null);
      return;
    }
    if (selectedAddressId && addresses.some((item) => Number(item.id) === selectedAddressId)) {
      return;
    }
    const fallback = addresses.find((item) => Boolean(item.is_default)) || addresses[0];
    setSelectedAddressId(Number(fallback.id));
  }, [addresses, selectedAddressId]);

  useEffect(() => {
    if (status !== "authenticated" || !user || !program?.vip) {
      return;
    }
    const nextPoints = Number(program.vip.current_points ?? user.points_total ?? 0);
    const currentPoints = Number(user.points_total || 0);
    if (nextPoints !== currentPoints) {
      void refreshUser();
    }
  }, [program?.vip, refreshUser, status, user]);

  const categories = useMemo(() => {
    const names = Array.from(
      new Set(
        rewards
          .map((item) => item.category?.trim())
          .filter((value): value is string => Boolean(value)),
      ),
    );
    return ["All", ...names];
  }, [rewards]);

  const filteredRewards = useMemo(() => {
    return rewards.filter((item) => {
      const matchesSearch =
        !search ||
        `${item.title} ${item.description || ""} ${item.category || ""}`
          .toLowerCase()
          .includes(search.toLowerCase());
      const matchesCategory = category === "All" || item.category === category;
      const cost = Number(item.points_cost || 0);
      const matchesRange =
        range === "All" ||
        (range === "0–500" && cost <= 500) ||
        (range === "500–1000" && cost > 500 && cost <= 1000) ||
        (range === "1000–3000" && cost > 1000 && cost <= 3000) ||
        (range === "3000+" && cost > 3000);
      return matchesSearch && matchesCategory && matchesRange;
    });
  }, [category, range, rewards, search]);

  const availablePoints = isAuthenticated ? Number(user?.points_balance ?? 0) : 0;
  const pendingPoints = Math.max(Number(user?.points_pending ?? 0), 0);
  const vip = program?.vip;
  const tierProgress = Math.max(0, Math.min(100, Math.round(Number(vip?.progress_ratio ?? 0) * 100)));
  const totalPoints = Number(vip?.current_points ?? user?.points_total ?? availablePoints);
  const confirmedVideos = Number(vip?.confirmed_videos ?? 0);
  const pointTarget = Math.max(1, Number(vip?.is_top_tier ? vip?.threshold_points : vip?.next_threshold_points) || 1);
  const videoTarget = Math.max(1, Number(vip?.is_top_tier ? vip?.threshold_videos : vip?.next_threshold_videos) || 1);
  const pointLaneProgress = Math.max(0, Math.min(100, Math.round((totalPoints / pointTarget) * 100)));
  const videoLaneProgress = Math.max(0, Math.min(100, Math.round((confirmedVideos / videoTarget) * 100)));
  const tierStartLabel = vip?.tier_label || t("redeem.tierCreator");
  const tierEndLabel = vip?.is_top_tier ? t("redeem.topTierLabel") : vip?.next_tier_label || t("redeem.tierPro");
  const tierRequirements = [
    Number(vip?.points_to_next ?? 0) > 0 ? t("redeem.pointsRemainingOnly", { points: Number(vip?.points_to_next ?? 0).toLocaleString() }) : "",
    Number(vip?.videos_to_next ?? 0) > 0 ? t("redeem.videosRemainingOnly", { videos: Number(vip?.videos_to_next ?? 0).toLocaleString() }) : "",
  ].filter(Boolean).join(" · ");
  const tierStatusLabel =
    !vip
      ? t("redeem.signInToViewBalance")
      : vip.is_top_tier
        ? t("redeem.topTierReached")
        : t("redeem.toNextTier", {
            tier: vip.next_tier_label || t("redeem.tierPro"),
            requirements: tierRequirements || t("redeem.pointsRemainingOnly", { points: Number(vip.points_to_next ?? 0).toLocaleString() }),
          });
  const periodSignals = useMemo(() => {
    const summarize = (nextPeriod: RankPeriod) => {
      const scoped = submissions.filter((item) => isValidSubmission(item) && isSubmissionInPeriod(item, nextPeriod));
      const points = scoped.reduce((sum, item) => sum + Number(item.points_awarded || 0), 0);
      if (!isAuthenticated) {
        return null;
      }
      return {
        videos: scoped.length,
        points,
      };
    };
    return {
      month: summarize("month"),
      year: summarize("year"),
    };
  }, [isAuthenticated, submissions]);
  const cartCost = Number(cartReward?.points_cost || 0);
  const cartHasEnoughPoints = availablePoints >= cartCost;

  function closeCart() {
    setCartOpen(false);
    setCartReward(null);
    setCartStep("review");
    setCartBusy(false);
    setCartMessage(null);
  }

  async function refreshAddresses() {
    if (!token) {
      setAddresses([]);
      setSelectedAddressId(null);
      return;
    }
    const nextAddresses = await listCreatorAddresses(token);
    setAddresses(nextAddresses);
  }

  async function openCartForReward(item: RewardItem) {
    setCartMessage(null);
    setSurfaceMessage(null);
    if (isRewardSoldOut(item)) {
      setSurfaceMessage({ tone: "warning", body: t("redeem.outOfStock") });
      return;
    }
    if (status !== "authenticated" || !token) {
      setSurfaceMessage({ tone: "warning", body: t("redeem.signInFirst") });
      openAuthModal("signin");
      return;
    }
    try {
      await refreshAddresses();
      setCartStep("review");
      setCartReward(item);
      setCartOpen(true);
    } catch (error) {
      setSurfaceMessage({ tone: "danger", body: error instanceof Error ? error.message : t("redeem.prepareCheckoutFailed") });
    }
  }

  function continueCartFlow() {
    if (!cartReward) {
      return;
    }
    if (!cartHasEnoughPoints) {
      setCartMessage({ tone: "danger", body: t("redeem.insufficientPoints") });
      return;
    }
    setCartMessage(null);
    setCartStep("address");
  }

  async function checkoutReward() {
    if (!cartReward || !token) {
      return;
    }
    if (!selectedAddressId) {
      setCartMessage({ tone: "warning", body: t("redeem.addAddressFirst") });
      return;
    }
    setCartBusy(true);
    setCartMessage(null);
    try {
      const response = await apiFetch<{ status?: string; message?: string }>(
        "/api/creator/redeem",
        {
          method: "POST",
          body: jsonBody({
            reward_id: cartReward.id,
            address_id: selectedAddressId,
          }),
        },
        token,
      );
      if (response.status !== "success") {
        throw new Error(response.message || t("redeem.redemptionFailed"));
      }
      await refreshUser();
      setProgram(await listCreatorProgram(token));
      await refreshAddresses();
      closeCart();
      navigate("/account?tab=orders");
    } catch (error) {
      setCartMessage({ tone: "danger", body: error instanceof Error ? error.message : t("redeem.redemptionFailed") });
    } finally {
      setCartBusy(false);
    }
  }

  return (
    <div className="bw-app bw-app--rewards">
      <BwTopNav active="rewards" user={user} points={status === "authenticated" ? availablePoints : undefined} />
      <main className="bw-page bw-page--rewards">
      {surfaceMessage ? <div className={`inline-message inline-message--${surfaceMessage.tone}`}>{surfaceMessage.body}</div> : null}

      <section className="bw-rewards-hero">
        <small>{t("redeem.availablePoints")}</small>
        <div className="bw-rewards-balance">{isAuthenticated ? availablePoints.toLocaleString() : "—"}</div>
        {isAuthenticated ? (
          pendingPoints > 0 ? <p>{t("redeem.pendingRelease", { points: pendingPoints.toLocaleString() })}</p> : <p>{t("redeem.readyToRedeem")}</p>
        ) : (
          <p>{t("redeem.signInToViewBalance")}</p>
        )}
        <div className="bw-tier-progress">
          <div className="bw-tier-progress__labels">
            <span>{tierStartLabel}</span>
            <span>{tierStatusLabel}</span>
            <span>{tierEndLabel}</span>
          </div>
          <div className="bw-tier-progress__track">
            <div style={{ width: `${tierProgress}%` }} />
          </div>
          <div className="bw-tier-lanes" aria-label="VIP points and video progress">
            <div className="bw-tier-lane">
              <div className="bw-tier-lane__head">
                <span>{t("redeem.validVideos")}</span>
                <b>{confirmedVideos.toLocaleString()} / {videoTarget.toLocaleString()}</b>
              </div>
              <div className="bw-tier-lane__track">
                <div style={{ width: `${videoLaneProgress}%` }} />
              </div>
            </div>
            <div className="bw-tier-lane">
              <div className="bw-tier-lane__head">
                <span>{t("redeem.periodPoints")}</span>
                <b>{totalPoints.toLocaleString()} / {pointTarget.toLocaleString()}</b>
              </div>
              <div className="bw-tier-lane__track">
                <div style={{ width: `${pointLaneProgress}%` }} />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bw-period-signal-strip">
        <article className="bw-period-signal-card">
          <small>{t("redeem.thisMonth")}</small>
          {periodSignals.month ? (
            <>
              <strong>{periodSignals.month.videos.toLocaleString()}</strong>
              <span>{t("redeem.validVideos")}</span>
              <em>{periodSignals.month.points.toLocaleString()} {t("redeem.pointsShort")} · {t("redeem.periodPoints")}</em>
            </>
          ) : (
            <>
              <strong>—</strong>
              <span>{t("redeem.signInToViewPeriodTitle")}</span>
              <em>{t("redeem.signInToViewPeriodBody")}</em>
            </>
          )}
        </article>
        <article className="bw-period-signal-card">
          <small>{t("redeem.thisYear")}</small>
          {periodSignals.year ? (
            <>
              <strong>{periodSignals.year.videos.toLocaleString()}</strong>
              <span>{t("redeem.validVideos")}</span>
              <em>{periodSignals.year.points.toLocaleString()} {t("redeem.pointsShort")} · {t("redeem.periodPoints")}</em>
            </>
          ) : (
            <>
              <strong>—</strong>
              <span>{t("redeem.signInToViewPeriodTitle")}</span>
              <em>{t("redeem.signInToViewPeriodBody")}</em>
            </>
          )}
        </article>
      </section>

      <div className="bw-rewards-layout">
        <section className="bw-rewards-main">
          <div className="bw-filter-stack">
            <div className="bw-filter-row">
              {categories.map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`bw-filter-pill${category === value ? " is-active" : ""}`}
                  onClick={() => setCategory(value)}
                >
                  {value === "All" ? t("redeem.filterAll") : value}
                </button>
              ))}
            </div>
            <div className="bw-filter-row bw-filter-row--compact">
              {rangeOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`bw-filter-pill${range === option.value ? " is-active" : ""}`}
                  onClick={() => setRange(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <input
              className="bw-rewards-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("redeem.searchPlaceholder")}
            />
          </div>

          <div className="bw-reward-grid">
            {loadingRewards ? (
              <div className="legacy-empty-row">{t("redeem.loadingRewards")}</div>
            ) : filteredRewards.length ? (
              filteredRewards.map((item) => (
                <article key={item.id} className="bw-reward-card">
                  <div className="bw-reward-card__media">
                    {item.image_url ? <img src={item.image_url} alt={item.title} className="reward-thumb-image" /> : <span>{rewardImageLabel(item)}</span>}
                    {Number(item.stock ?? 0) > 0 && Number(item.stock ?? 0) <= 5 ? <i>{item.stock} left</i> : null}
                  </div>
                  <div className="bw-reward-card__body">
                    <small>{item.category || t("redeem.rewardFallbackCategory")}</small>
                    <h3>{item.title}</h3>
                    <p>{rewardDescription(item)}</p>
                    <div className="bw-reward-card__foot">
                      <div className="bw-reward-card__price">{Number(item.points_cost || 0).toLocaleString()} {t("redeem.pointsShort")}</div>
                      <button
                        type="button"
                        className="bw-reward-card__action"
                        disabled={isRewardSoldOut(item)}
                        onClick={() => void openCartForReward(item)}
                      >
                        {isRewardSoldOut(item) ? t("redeem.soldOutAction") : t("redeem.redeemAction")}
                      </button>
                    </div>
                  </div>
                </article>
              ))
            ) : (
              <div className="legacy-empty-row">{t("redeem.noRewards")}</div>
            )}
          </div>
        </section>

        <aside className="bw-leaderboard-card">
          <div className="bw-leaderboard-card__head">
            <div>
              <small>{period === "month" ? t("redeem.month") : t("redeem.year")}</small>
              <h2>{t("redeem.rankTitle")}</h2>
            </div>
            <div className="bw-leaderboard-card__toggle">
              <button type="button" className={period === "month" ? "is-active" : ""} onClick={() => setPeriod("month")}>
                {t("redeem.month")}
              </button>
              <button type="button" className={period === "year" ? "is-active" : ""} onClick={() => setPeriod("year")}>
                {t("redeem.year")}
              </button>
            </div>
          </div>

          <div className="bw-leaderboard-list">
            {loadingLeaderboard ? (
              <div className="legacy-empty-row">{t("redeem.loadingRanking")}</div>
            ) : leaderboard.length ? (
              leaderboard.slice(0, 30).map((entry, index) => {
                const preview = buildLeaderboardViewerData(entry, index + 1);
                const hasMedia = Boolean(preview.uploadedVideoUrl || preview.posterUrl || preview.externalLinks.length);
                const actionLabel = preview.uploadedVideoUrl
                  ? t("redeem.playAction")
                  : preview.posterUrl || preview.externalLinks.length
                    ? t("redeem.previewAction")
                    : t("redeem.noMediaAction");
                return (
                  <div key={`${rewardHandle(entry)}-${index}`} className={`bw-leaderboard-row${index < 3 ? " is-top" : ""}`}>
                    <span className="bw-leaderboard-row__rank">{String(index + 1).padStart(2, "0")}</span>
                    <div className="bw-leaderboard-row__copy">
                      <strong>{entry.display_name || rewardHandle(entry)}</strong>
                      <small>
                        {entry.platform || t("redeem.creatorFallback")} · {entry.creator_code || t("redeem.noCreatorId")} · {rewardSubmissionCount(entry)} {t("redeem.subsShort")}
                      </small>
                    </div>
                    <span className="bw-leaderboard-row__score">{Number(rewardScore(entry)).toLocaleString()}</span>
                    <button type="button" className="bw-leaderboard-row__action" disabled={!hasMedia} onClick={() => setViewerData(preview)}>
                      {actionLabel}
                    </button>
                  </div>
                );
              })
            ) : (
              <div className="legacy-empty-row">{t("redeem.noRanking")}</div>
            )}
          </div>
        </aside>
      </div>

      <LegacyVideoViewer open={Boolean(viewerData)} data={viewerData} onClose={() => setViewerData(null)} />

      {cartOpen && cartReward ? (
        <div
          className="cart-modal show"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              closeCart();
            }
          }}
        >
          <div className="cart-sheet">
            <div className="sheet-top">
              <div>
                <h3>{t("redeem.checkoutTitle")}</h3>
                <p>{t("redeem.checkoutBody")}</p>
              </div>
              <button className="close" type="button" onClick={() => closeCart()}>
                ×
              </button>
            </div>

            <div className="cart-progress" aria-label={t("redeem.checkoutProgress")}>
              <span className={`cart-progress__step${cartStep === "review" ? " is-active" : ""}`}>{t("redeem.reviewStep")}</span>
              <span className={`cart-progress__step${cartStep === "address" ? " is-active" : ""}`}>{t("redeem.addressStep")}</span>
            </div>

            {cartMessage ? <div className={`inline-message inline-message--${cartMessage.tone}`}>{cartMessage.body}</div> : null}

            {cartStep === "review" ? (
              <div className="checkout-step">
                <div className="cart-step-copy">
                  <strong>{t("redeem.reviewStepTitle")}</strong>
                  <span>{t("redeem.reviewStepBody")}</span>
                </div>

                <div className="cart-list">
                  <article className="cart-row">
                    <div className="thumb">
                      {cartReward.image_url ? <img src={cartReward.image_url} alt={cartReward.title} className="reward-thumb-image" /> : rewardImageLabel(cartReward)}
                    </div>
                    <div className="cart-row__copy">
                      <strong>{cartReward.title}</strong>
                      <p>{rewardDescription(cartReward)}</p>
                      <small>{cartReward.category || t("redeem.rewardFallbackLabel")} · {cartCost.toLocaleString()} {t("redeem.pointsShort")}</small>
                    </div>
                  </article>
                </div>

                <div className="cart-summary">
                  <strong>{t("redeem.checkoutSummaryTitle")}</strong>
                  <span>{t("redeem.checkoutSummaryBody", { points: cartCost.toLocaleString() })}</span>
                  <span>{t("redeem.availableBalanceSummary", { points: availablePoints.toLocaleString() })}</span>
                </div>
              </div>
            ) : (
              <div className="checkout-step">
                <div className="cart-step-copy">
                  <strong>{t("redeem.addressStepTitle")}</strong>
                  <span>{t("redeem.addressStepBody")}</span>
                </div>

                {!addresses.length ? (
                  <EmptyState
                    title={t("redeem.missingAddressTitle")}
                    body={t("redeem.missingAddressBody")}
                    action={
                      <button
                        className="primary-button"
                        type="button"
                        onClick={() => {
                          closeCart();
                          navigate("/account");
                        }}
                      >
                        {t("redeem.openAccount")}
                      </button>
                    }
                  />
                ) : (
                  <div className="checkout-address-list">
                    <strong className="section-mini-head">{t("redeem.selectShipping")}</strong>
                    {addresses.map((address) => (
                      <label key={address.id} className={`checkout-address-row${selectedAddressId === Number(address.id) ? " active" : ""}`}>
                        <input
                          type="radio"
                          name="checkout-address"
                          checked={selectedAddressId === Number(address.id)}
                          onChange={() => setSelectedAddressId(Number(address.id))}
                        />
                        <span>
                          <b>{address.name || user?.name || t("redeem.creatorFallback")}</b>
                          <br />
                          {[address.address1, address.address2, address.city, address.state, address.postal_code, address.country]
                            .filter(Boolean)
                            .join(", ") || t("redeem.addressPending")}
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="cart-actions">
              {cartStep === "review" ? (
                <>
                  <button className="outline-btn" type="button" onClick={() => closeCart()}>
                    {t("redeem.cancel")}
                  </button>
                  <button className="black-btn" type="button" onClick={() => continueCartFlow()}>
                    {t("redeem.continueToShipping")}
                  </button>
                </>
              ) : (
                <>
                  <button className="outline-btn" type="button" onClick={() => setCartStep("review")}>
                    {t("redeem.backToReward")}
                  </button>
                  <button className="black-btn" type="button" disabled={cartBusy || !addresses.length} onClick={() => void checkoutReward()}>
                    {cartBusy ? t("redeem.processing") : t("redeem.confirmShipment")}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
      <FloatingViaCat />
      </main>
    </div>
  );
}
