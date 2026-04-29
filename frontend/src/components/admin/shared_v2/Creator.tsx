/**
 * Tier / VID helpers — shared display utilities
 */
import type { CSSProperties } from "react";

export type VipTier = "student" | "bronze" | "silver" | "gold" | "platinum";

export const TIER_LABELS: Record<VipTier, string> = {
  student: "Student",
  bronze: "Bronze",
  silver: "Silver",
  gold: "Gold",
  platinum: "Platinum",
};

export const TIER_MULTIPLIERS: Record<VipTier, string> = {
  student: "—",
  bronze: "1.0×",
  silver: "1.2×",
  gold: "1.5×",
  platinum: "2.0×",
};

export const TIER_REQUIREMENTS: Record<VipTier, { videos: number; points: number; commission: number }> = {
  student: { videos: 0, points: 0, commission: 0 },
  bronze: { videos: 1, points: 0, commission: 0.05 },
  silver: { videos: 3, points: 500, commission: 0.06 },
  gold: { videos: 8, points: 2000, commission: 0.07 },
  platinum: { videos: 20, points: 5000, commission: 0.1 },
};

/** Derive tier from video + points count (simple, client-side for display only) */
export function deriveTier(validVideos: number, points: number): VipTier {
  if (validVideos >= 20 && points >= 5000) return "platinum";
  if (validVideos >= 8 && points >= 2000) return "gold";
  if (validVideos >= 3 && points >= 500) return "silver";
  if (validVideos >= 1) return "bronze";
  return "student";
}

/** Format VID from a user ID number or string */
export function formatVID(id: number | string | null | undefined): string {
  if (id === null || id === undefined || id === "") return "V_???";
  const n = typeof id === "number" ? id : parseInt(String(id).replace(/[^0-9]/g, ""), 10);
  if (Number.isNaN(n)) return `V_${String(id).slice(0, 6)}`;
  return `V_${String(n).padStart(6, "0")}`;
}

/** <TierDot tier="gold" /> */
export function TierDot({ tier, style }: { tier: VipTier; style?: CSSProperties }) {
  return (
    <span
      aria-hidden
      style={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: `var(--ax-vip-${tier})`,
        display: "inline-block",
        flexShrink: 0,
        ...style,
      }}
    />
  );
}

/** <TierBadge tier="gold" /> — larger badge for detail panels */
export function TierBadge({ tier }: { tier: VipTier }) {
  return (
    <span className="ax-tier-badge">
      <TierDot tier={tier} />
      {TIER_LABELS[tier].toUpperCase()}
    </span>
  );
}

/** VID + @handle two-line label */
export function CreatorIdCell({
  vid,
  handle,
  avatar,
}: {
  vid: string;
  handle?: string | null;
  avatar?: string | null;
}) {
  const avatarText = (handle || vid || "?").replace(/^[@V_]/, "").charAt(0).toUpperCase();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span className="ax-avatar">{avatar || avatarText}</span>
      <div style={{ minWidth: 0 }}>
        <div
          className="ax-mono"
          style={{
            color: "var(--ax-text-5)",
            fontWeight: 500,
            fontSize: 11,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {vid}
        </div>
        {handle ? (
          <div
            style={{
              color: "var(--ax-text-1)",
              fontSize: 9,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            @{handle.replace(/^@/, "")}
          </div>
        ) : null}
      </div>
    </div>
  );
}
