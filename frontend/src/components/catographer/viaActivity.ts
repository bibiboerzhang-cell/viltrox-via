import type { CatAction } from "./viaScenePlaybook";

export interface ViaActivityState {
  version?: string;
  id?: string;
  label?: string;
  movement_mode?: string;
  render_mode?: string;
  canonical_pose?: string;
  prop_kit?: string;
  motion_clip?: string;
  anchor_hint?: string;
  scene_line?: string;
  ambient_lines?: string[];
  frame_count?: number;
  frames?: string[];
  sheet_url?: string;
}

const VIA_ACTIVITY_FRAME_COUNT = 6;

const ACTIVITY_FALLBACKS: Record<string, ViaActivityState> = {
  "page-patrol": {
    id: "page-patrol",
    label: "Page patrol",
    movement_mode: "patrol",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "none",
    motion_clip: "idle-breathe",
    anchor_hint: "page-grid",
    scene_line: "Via is roaming the page like a tiny set cat, chasing light, cables, and the next thing worth commenting on.",
  },
  "shoot-camera": {
    id: "shoot-camera",
    label: "Shoot camera",
    movement_mode: "pace",
    render_mode: "canonical_pose",
    canonical_pose: "side",
    prop_kit: "camera-body",
    motion_clip: "camera-ready",
    anchor_hint: "submission-composer",
    scene_line: "Via is actively shooting, pacing between marks with a compact camera rig in paw and a little too much confidence.",
  },
  "monitor-review": {
    id: "monitor-review",
    label: "Monitor review",
    movement_mode: "anchor",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "monitor-station",
    motion_clip: "review-lean",
    anchor_hint: "system-progress",
    scene_line: "Via is parked by a monitor and playback station, quietly checking whether the take really landed.",
  },
  "push-chapman-cart": {
    id: "push-chapman-cart",
    label: "Push Chapman cart",
    movement_mode: "track",
    render_mode: "canonical_pose",
    canonical_pose: "side",
    prop_kit: "chapman-cart",
    motion_clip: "cart-walk",
    anchor_hint: "page-floor",
    scene_line: "Via is trotting beside a Chapman cart with an ARRI body and Viltrox long zoom gear riding shotgun.",
  },
  "mini-lf-epic": {
    id: "mini-lf-epic",
    label: "Mini LF plus EPIC",
    movement_mode: "anchor",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "mini-lf-epic-rig",
    motion_clip: "focus-check",
    anchor_hint: "via-card",
    scene_line: "Via is operating around a Mini LF plus EPIC setup, checking monitor, lens, and the shape of the anamorphic take.",
  },
  "sony-z-flash": {
    id: "sony-z-flash",
    label: "Sony with Z1 and Z2 flash",
    movement_mode: "burst",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "sony-z-flash",
    motion_clip: "flash-pop",
    anchor_hint: "submission-composer",
    scene_line: "Via is firing quick Sony test frames with Z1 and Z2 flashes nearby, checking punch, falloff, and the fun part.",
  },
  "sony-air-pack": {
    id: "sony-air-pack",
    label: "Sony AIR pack",
    movement_mode: "pace",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "air-kit",
    motion_clip: "bag-check",
    anchor_hint: "submission-composer",
    scene_line: "Via is packing and testing a lightweight Sony plus AIR travel kit that still feels ambitious enough to leave the house for.",
  },
  "fujifilm-air-scout": {
    id: "fujifilm-air-scout",
    label: "Fujifilm AIR scout",
    movement_mode: "roam",
    render_mode: "canonical_pose",
    canonical_pose: "side",
    prop_kit: "air-camera",
    motion_clip: "patrol-walk",
    anchor_hint: "page-grid",
    scene_line: "Via is wandering with a Fujifilm and AIR lens combo like a tiny street shooter scouting corners and reflections.",
  },
  "lab-portrait-bench": {
    id: "lab-portrait-bench",
    label: "LAB portrait bench",
    movement_mode: "anchor",
    render_mode: "canonical_pose",
    canonical_pose: "front",
    prop_kit: "lab-bench",
    motion_clip: "hero-review",
    anchor_hint: "via-card",
    scene_line: "Via is leaning into a premium portrait bench with LAB glass close by, checking the frame for separation and polish.",
  },
  "pro-evo-roam": {
    id: "pro-evo-roam",
    label: "PRO and EVO roam",
    movement_mode: "pace",
    render_mode: "canonical_pose",
    canonical_pose: "side",
    prop_kit: "pro-evo-kit",
    motion_clip: "compare-walk",
    anchor_hint: "page-grid",
    scene_line: "Via is alternating between PRO and EVO glass, walking the room to feel out whether the job wants fast polish or everyday agility.",
  },
};

function buildFrames(activityId: string): string[] {
  return Array.from({ length: VIA_ACTIVITY_FRAME_COUNT }, (_, index) => `/cat/working/${activityId}/frame-${String(index).padStart(2, "0")}.svg`);
}

export function normalizeViaActivityState(activity?: ViaActivityState | null): ViaActivityState | null {
  const activityId = String(activity?.id || "").trim() || "page-patrol";
  const fallback = ACTIVITY_FALLBACKS[activityId] || ACTIVITY_FALLBACKS["page-patrol"];
  const renderMode = String(activity?.render_mode || fallback.render_mode || "canonical_pose").trim() || "canonical_pose";
  const frames =
    renderMode === "scene_sprite"
      ? Array.isArray(activity?.frames) && activity?.frames.length
        ? activity.frames.map((item) => String(item || "").trim()).filter(Boolean)
        : buildFrames(activityId)
      : Array.isArray(activity?.frames)
        ? activity.frames.map((item) => String(item || "").trim()).filter(Boolean)
        : [];

  return {
    version: activity?.version || "2026-04-14",
    id: activityId,
    label: activity?.label || fallback.label,
    movement_mode: activity?.movement_mode || fallback.movement_mode,
    render_mode: renderMode,
    canonical_pose: String(activity?.canonical_pose || fallback.canonical_pose || "front"),
    prop_kit: String(activity?.prop_kit || fallback.prop_kit || "none"),
    motion_clip: String(activity?.motion_clip || fallback.motion_clip || "idle-breathe"),
    anchor_hint: String(activity?.anchor_hint || fallback.anchor_hint || "page-grid"),
    scene_line: activity?.scene_line || fallback.scene_line,
    ambient_lines: Array.isArray(activity?.ambient_lines) ? activity.ambient_lines : [],
    frame_count: renderMode === "scene_sprite" ? Number(activity?.frame_count || frames.length || VIA_ACTIVITY_FRAME_COUNT) : 0,
    frames,
    sheet_url: renderMode === "scene_sprite" ? activity?.sheet_url || `/cat/working/${activityId}/sheet.svg` : "",
  };
}

export function resolveFallbackActivity(text?: string | null): ViaActivityState {
  const haystack = String(text || "").trim().toLowerCase();
  if (haystack.includes("epic") || haystack.includes("mini lf") || haystack.includes("anamorphic")) {
    return normalizeViaActivityState({ id: "mini-lf-epic" }) as ViaActivityState;
  }
  if (haystack.includes("chapman") || haystack.includes("luna") || haystack.includes("cart") || haystack.includes("dolly")) {
    return normalizeViaActivityState({ id: "push-chapman-cart" }) as ViaActivityState;
  }
  if (haystack.includes("z1") || haystack.includes("z2") || haystack.includes("flash")) {
    return normalizeViaActivityState({ id: "sony-z-flash" }) as ViaActivityState;
  }
  if (haystack.includes("fujifilm") || haystack.includes("fuji")) {
    return normalizeViaActivityState({ id: "fujifilm-air-scout" }) as ViaActivityState;
  }
  if (haystack.includes("lab")) {
    return normalizeViaActivityState({ id: "lab-portrait-bench" }) as ViaActivityState;
  }
  if (haystack.includes("pro") || haystack.includes("evo")) {
    return normalizeViaActivityState({ id: "pro-evo-roam" }) as ViaActivityState;
  }
  if (haystack.includes("air") || haystack.includes("sony")) {
    return normalizeViaActivityState({ id: "sony-air-pack" }) as ViaActivityState;
  }
  if (haystack.includes("upload") || haystack.includes("review") || haystack.includes("score")) {
    return normalizeViaActivityState({ id: "monitor-review" }) as ViaActivityState;
  }
  return normalizeViaActivityState({ id: "page-patrol" }) as ViaActivityState;
}

export function activityToCatAction(activity?: ViaActivityState | null, speaking = false): CatAction {
  if (speaking) {
    return "talking";
  }
  switch (String(activity?.movement_mode || "").trim().toLowerCase()) {
    case "track":
      return "carting";
    case "burst":
      return "flash";
    case "roam":
    case "patrol":
      return "play";
    case "pace":
      return "shooting";
    case "anchor":
      return "filming";
    default:
      return "idle";
  }
}

export function activityLabel(activity?: ViaActivityState | null): string {
  return String(activity?.label || "Via working").trim();
}
