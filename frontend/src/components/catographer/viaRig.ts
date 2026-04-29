import type { ViaActivityState } from "./viaActivity";
import type { CatAction, CatPose } from "./viaScenePlaybook";

export type ViaRenderMode = "canonical_pose" | "scene_sprite" | "glb_rig";

export interface ViaRigPlan {
  renderMode: ViaRenderMode;
  basePose: CatPose;
  assetUrl: string;
  propKit: string;
  motionClip: string;
  anchorHint: string;
}

const POSE_ASSET_MAP: Record<CatPose, string> = {
  front: "/cat/via-front.png",
  side: "/cat/via-side.png",
  back: "/cat/via-back.png",
};

function normalizePose(value?: string | null): CatPose {
  if (value === "side" || value === "back") {
    return value;
  }
  return "front";
}

function normalizeRenderMode(value?: string | null): ViaRenderMode {
  if (value === "scene_sprite" || value === "glb_rig") {
    return value;
  }
  return "canonical_pose";
}

function inferPoseFromAction(action?: CatAction): CatPose {
  switch (action) {
    case "running":
    case "shooting":
    case "carting":
    case "play":
      return "side";
    default:
      return "front";
  }
}

export function resolveViaRigPlan(activity?: ViaActivityState | null, action?: CatAction): ViaRigPlan {
  const renderMode = normalizeRenderMode(activity?.render_mode);
  const basePose = normalizePose(activity?.canonical_pose) || inferPoseFromAction(action);
  const propKit = String(activity?.prop_kit || "none").trim() || "none";
  const motionClip = String(activity?.motion_clip || "idle-breathe").trim() || "idle-breathe";
  const anchorHint = String(activity?.anchor_hint || "page-grid").trim() || "page-grid";

  return {
    renderMode,
    basePose: renderMode === "canonical_pose" ? basePose : inferPoseFromAction(action),
    assetUrl: POSE_ASSET_MAP[renderMode === "canonical_pose" ? basePose : inferPoseFromAction(action)],
    propKit,
    motionClip,
    anchorHint,
  };
}
