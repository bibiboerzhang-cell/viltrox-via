import { useMemo } from "react";
import { motion } from "framer-motion";
import { CatAction, CatPose } from "./viaScenePlaybook";
import { activityToCatAction, normalizeViaActivityState, type ViaActivityState } from "./viaActivity";

type CatMode = "panel" | "floating";

const ACTION_ANIMATION: Record<CatAction, Record<string, number[] | number>> = {
  idle: { y: [0, -5, 0], rotate: [0, -1.5, 0, 1.5, 0] },
  talking: { y: [0, -3, 0], rotate: [0, -1, 0, 1, 0], scale: [1, 1.02, 1] },
  running: { y: [0, -8, 0], rotate: [0, -4, 3, -2, 0], x: [0, 3, -3, 0] },
  shooting: { y: [0, -2, 0], rotate: [0, -2, 0, 2, 0], scale: [1, 1.03, 1] },
  filming: { y: [0, -4, 0], rotate: [0, -1.5, 1.5, 0], scale: [1, 1.01, 1] },
  thinking: { y: [0, -4, 0], rotate: [0, -3, 0, 1.5, 0], scale: [1, 1.01, 1] },
  carting: { y: [0, -7, 0], rotate: [0, -1.5, 1.5, 0], x: [0, 4, -2, 0] },
  flash: { y: [0, -3, 0], rotate: [0, -2.5, 0, 2.5, 0], scale: [1, 1.04, 1] },
  celebrating: { y: [0, -9, 0], rotate: [0, -3, 3, -1, 0], scale: [1, 1.06, 1] },
  play: { y: [0, -7, 0], rotate: [0, -5, 2, -4, 0], x: [0, 4, -1, 0] },
};

const ACTION_AURA: Record<CatAction, string> = {
  idle: "from-orange-100/80 via-amber-50/20 to-transparent",
  talking: "from-rose-100/80 via-orange-50/20 to-transparent",
  running: "from-orange-200/80 via-amber-100/20 to-transparent",
  shooting: "from-amber-100/80 via-yellow-50/20 to-transparent",
  filming: "from-slate-200/70 via-orange-50/20 to-transparent",
  thinking: "from-blue-100/80 via-cyan-50/20 to-transparent",
  carting: "from-orange-200/80 via-stone-100/20 to-transparent",
  flash: "from-yellow-100/90 via-orange-100/35 to-transparent",
  celebrating: "from-pink-100/80 via-orange-100/25 to-transparent",
  play: "from-emerald-100/80 via-lime-50/20 to-transparent",
};

export function CatAvatar({
  pose = "front",
  speaking = false,
  action,
  mode = "panel",
  mirror = false,
  activityState,
  showBadge = true,
}: {
  pose?: CatPose;
  speaking?: boolean;
  action?: CatAction;
  mode?: CatMode;
  mirror?: boolean;
  activityState?: ViaActivityState | null;
  showBadge?: boolean;
}) {
  const normalizedActivity = useMemo(() => normalizeViaActivityState(activityState), [activityState]);
  const currentAction = action ?? activityToCatAction(normalizedActivity, speaking);
  const isFloating = mode === "floating";
  const resolvedPose: CatPose = useMemo(() => {
    if (pose !== "front") {
      return pose;
    }
    if (currentAction === "running" || currentAction === "filming" || currentAction === "shooting" || currentAction === "carting") {
      return "side";
    }
    return "front";
  }, [currentAction, pose]);
  const currentSprite = `/cat/via-${resolvedPose}.png`;

  return (
    <motion.div
      className={
        isFloating
          ? "relative flex h-44 w-36 items-end justify-center"
          : "relative flex h-32 w-28 items-end justify-center"
      }
      animate={ACTION_ANIMATION[currentAction]}
      transition={{ duration: currentAction === "running" ? 0.72 : 2.4, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
    >
      <div className={`pointer-events-none absolute inset-x-2 top-6 h-24 rounded-full bg-gradient-to-b ${ACTION_AURA[currentAction]} blur-2xl`} />
      <img
        src={currentSprite}
        alt="Via cat avatar"
        className={`${isFloating ? "h-[156px] w-[156px] object-contain drop-shadow-[0_24px_28px_rgba(15,23,42,0.22)]" : "h-[118px] w-[118px] object-contain drop-shadow-[0_18px_22px_rgba(15,23,42,0.18)]"}${mirror ? " -scale-x-100" : ""}`}
        loading="lazy"
        onError={(event) => {
          event.currentTarget.style.display = "none";
        }}
      />
      {showBadge ? (
        <div
          className={
            isFloating
              ? "pointer-events-none absolute bottom-0 rounded-full bg-black/75 px-3 py-1 text-[11px] font-bold tracking-[0.18em] text-white shadow-[0_10px_24px_rgba(15,23,42,0.2)]"
              : "pointer-events-none absolute bottom-1 rounded-full bg-black/70 px-3 py-1 text-[11px] font-bold tracking-[0.18em] text-white"
          }
        >
          VIA
        </div>
      ) : null}
    </motion.div>
  );
}
