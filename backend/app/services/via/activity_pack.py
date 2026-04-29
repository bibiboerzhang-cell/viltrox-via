from __future__ import annotations

from typing import Any


VIA_ACTIVITY_VERSION = "2026-04-14"
VIA_ACTIVITY_FRAME_COUNT = 6
VIA_ACTIVITY_BASE_URL = "/cat/working"


def _activity_frames(activity_id: str, *, base_url: str = VIA_ACTIVITY_BASE_URL) -> list[str]:
    safe_base = base_url.rstrip("/")
    return [f"{safe_base}/{activity_id}/frame-{index:02d}.svg" for index in range(VIA_ACTIVITY_FRAME_COUNT)]


_ACTIVITY_DEFS: list[dict[str, Any]] = [
    {
        "id": "page-patrol",
        "label": "Page patrol",
        "movement_mode": "patrol",
        "canonical_pose": "front",
        "prop_kit": "none",
        "motion_clip": "idle-breathe",
        "anchor_hint": "page-grid",
        "scene_line": "Via is roaming the page like a tiny set cat, chasing light, cables, and the next thing worth commenting on.",
        "ambient_lines": [
            "I am doing a slow lap around the page so nobody forgets the lens cap.",
            "Tiny status update: I am pretending the whole page is my soundstage again.",
            "I just inspected a suspiciously loose strap. Everything is still adorable and under control.",
            "If you leave me alone too long, I start scouting corners like I am location managing.",
        ],
        "keywords": ["patrol", "pet", "cat", "mascot", "play", "cute", "haha", "哈哈"],
    },
    {
        "id": "shoot-camera",
        "label": "Shoot camera",
        "movement_mode": "pace",
        "canonical_pose": "side",
        "prop_kit": "camera-body",
        "motion_clip": "camera-ready",
        "anchor_hint": "submission-composer",
        "scene_line": "Via is actively shooting, pacing between marks with a compact camera rig in paw and a little too much confidence.",
        "ambient_lines": [
            "I am stealing a few test frames while the humans argue about the shot list.",
            "My paws are on camera duty right now, so please imagine a very professional tail flick.",
            "I keep reframing the room like the next hero shot might be hiding in a corner.",
            "There is a lens in my paw and an unreasonable amount of confidence in my whiskers.",
        ],
        "keywords": ["shoot", "camera", "拍摄", "拍片", "photo", "摄影", "creator", "content", "shot"],
    },
    {
        "id": "monitor-review",
        "label": "Monitor review",
        "movement_mode": "anchor",
        "canonical_pose": "front",
        "prop_kit": "monitor-station",
        "motion_clip": "review-lean",
        "anchor_hint": "system-progress",
        "scene_line": "Via is parked by a monitor and playback station, quietly checking whether the take really landed.",
        "ambient_lines": [
            "I am peeking over the monitor like a tiny focus puller who knows something.",
            "Playback is up. I am doing my serious little review face now.",
            "I keep rewatching the opening seconds because that is where the rhythm gives itself away.",
            "My current job is staring at the monitor until the next better idea appears.",
        ],
        "keywords": ["monitor", "review", "upload", "playback", "video", "critique", "score", "analysis", "投稿"],
    },
    {
        "id": "push-chapman-cart",
        "label": "Push Chapman cart",
        "movement_mode": "track",
        "canonical_pose": "side",
        "prop_kit": "chapman-cart",
        "motion_clip": "cart-walk",
        "anchor_hint": "page-floor",
        "scene_line": "Via is trotting beside a Chapman cart with an ARRI body and Viltrox long zoom gear riding shotgun.",
        "ambient_lines": [
            "I am making a very small but very official Chapman cart lap right now.",
            "The cart is moving, the wheels are singing, and I am acting like first AC in cat form.",
            "One paw on the cart, one eye on the glass, zero tolerance for sloppy cable loops.",
            "I am escorting the cart like the LUNA zoom personally asked for security.",
        ],
        "keywords": ["chapman", "dolly", "cart", "arri", "luna", "30-300", "42-420", "cine", "cinema", "rental"],
    },
    {
        "id": "mini-lf-epic",
        "label": "Mini LF plus EPIC",
        "movement_mode": "anchor",
        "canonical_pose": "front",
        "prop_kit": "mini-lf-epic-rig",
        "motion_clip": "focus-check",
        "anchor_hint": "via-card",
        "scene_line": "Via is operating around a Mini LF plus EPIC setup, checking monitor, lens, and the shape of the anamorphic take.",
        "ambient_lines": [
            "I am up on tiptoe by the Mini LF, pretending I personally approved the EPIC pairing.",
            "The EPIC glass is on, the mood is cinematic, and I am taking my tiny playback duties seriously.",
            "I keep looking from the lens to the monitor like a cat who suddenly works in cinema.",
            "This is my anamorphic lane now. Please respect the whisker-level concentration.",
        ],
        "keywords": ["mini lf", "epic", "anamorphic", "pl", "cine prime", "t2.0"],
    },
    {
        "id": "sony-z-flash",
        "label": "Sony with Z1 and Z2 flash",
        "movement_mode": "burst",
        "canonical_pose": "front",
        "prop_kit": "sony-z-flash",
        "motion_clip": "flash-pop",
        "anchor_hint": "submission-composer",
        "scene_line": "Via is firing quick Sony test frames with Z1 and Z2 flashes nearby, checking punch, falloff, and the fun part.",
        "ambient_lines": [
            "I am popping little flash tests until the room agrees to look more glamorous.",
            "The Sony body is in paw, the Z flashes are sparking, and I feel annoyingly powerful.",
            "This is my portrait-light goblin hour.",
            "I just fired another flash test and acted like the lighting plan was obvious all along.",
        ],
        "keywords": ["sony", "z1", "z2", "flash", "strobe", "ttl", "portrait", "lighting"],
    },
    {
        "id": "sony-air-pack",
        "label": "Sony AIR pack",
        "movement_mode": "pace",
        "canonical_pose": "front",
        "prop_kit": "air-kit",
        "motion_clip": "bag-check",
        "anchor_hint": "submission-composer",
        "scene_line": "Via is packing and testing a lightweight Sony plus AIR travel kit that still feels ambitious enough to leave the house for.",
        "ambient_lines": [
            "I am sorting the tiny Sony travel bag like a cat who has opinions about prime spacing.",
            "AIR kit check: light bag, quick paws, no wasted glass.",
            "I am choosing between compact primes and acting like every gram matters.",
            "This kit is for moving fast and still looking suspiciously prepared.",
        ],
        "keywords": ["sony air", "air", "travel", "student", "budget", "fe", "e mount"],
    },
    {
        "id": "fujifilm-air-scout",
        "label": "Fujifilm AIR scout",
        "movement_mode": "roam",
        "canonical_pose": "side",
        "prop_kit": "air-camera",
        "motion_clip": "patrol-walk",
        "anchor_hint": "page-grid",
        "scene_line": "Via is wandering with a Fujifilm and AIR lens combo like a tiny street shooter scouting corners and reflections.",
        "ambient_lines": [
            "I am doing a little Fujifilm street walk, looking for reflections and excuses.",
            "The Fuji kit is light, the paws are quick, and I am nosing around for a better frame.",
            "I keep scouting the page like it is a quiet side street right before golden hour.",
            "This is my walkaround lane. I may find a composition before you do.",
        ],
        "keywords": ["fujifilm", "fuji", "xf", "x mount", "street", "walk", "travel"],
    },
    {
        "id": "lab-portrait-bench",
        "label": "LAB portrait bench",
        "movement_mode": "anchor",
        "canonical_pose": "front",
        "prop_kit": "lab-bench",
        "motion_clip": "hero-review",
        "anchor_hint": "via-card",
        "scene_line": "Via is leaning into a premium portrait bench with LAB glass close by, checking the frame for separation and polish.",
        "ambient_lines": [
            "I am giving the LAB setup my most expensive-looking review face.",
            "The LAB glass is on deck and I am pretending this entire bench belongs to my portfolio.",
            "This lane is all crisp detail, clean separation, and one very attentive little cat.",
            "I am monitoring the premium prime like the hero frame owes me rent.",
        ],
        "keywords": ["lab", "135 lab", "35 lab", "flagship", "premium", "hero portrait"],
    },
    {
        "id": "pro-evo-roam",
        "label": "PRO and EVO roam",
        "movement_mode": "pace",
        "canonical_pose": "side",
        "prop_kit": "pro-evo-kit",
        "motion_clip": "compare-walk",
        "anchor_hint": "page-grid",
        "scene_line": "Via is alternating between PRO and EVO glass, walking the room to feel out whether the job wants fast polish or everyday agility.",
        "ambient_lines": [
            "I am toggling between PRO and EVO energy like a tiny camera assistant with taste.",
            "Fast glass on one side, everyday glass on the other, and I am pacing between both like it matters deeply.",
            "This is my practical-versus-polished comparison walk.",
            "I am roaming with the PRO and EVO lane open in both paws.",
        ],
        "keywords": ["pro", "evo", "f1.4", "85mm f2.0", "everyday rig", "comparison"],
    },
]


def build_via_activity_pack(*, base_url: str = VIA_ACTIVITY_BASE_URL) -> dict[str, Any]:
    activities: dict[str, dict[str, Any]] = {}
    for item in _ACTIVITY_DEFS:
        render_mode = str(item.get("render_mode") or "canonical_pose").strip() or "canonical_pose"
        canonical_pose = str(item.get("canonical_pose") or "front").strip() or "front"
        prop_kit = str(item.get("prop_kit") or "none").strip() or "none"
        motion_clip = str(item.get("motion_clip") or "idle-breathe").strip() or "idle-breathe"
        anchor_hint = str(item.get("anchor_hint") or "page-grid").strip() or "page-grid"
        activity = {
            **item,
            "render_mode": render_mode,
            "canonical_pose": canonical_pose,
            "prop_kit": prop_kit,
            "motion_clip": motion_clip,
            "anchor_hint": anchor_hint,
            "frame_count": VIA_ACTIVITY_FRAME_COUNT,
            "frames": _activity_frames(item["id"], base_url=base_url),
            "sheet_url": f"{base_url.rstrip('/')}/{item['id']}/sheet.svg",
        }
        activities[item["id"]] = activity
    return {
        "version": VIA_ACTIVITY_VERSION,
        "default_activity_id": "monitor-review",
        "frame_count": VIA_ACTIVITY_FRAME_COUNT,
        "activities": activities,
    }


def _contains_any(haystack: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in haystack for keyword in keywords)


def resolve_via_activity_state(
    *,
    user_text: str = "",
    title: str = "",
    text: str = "",
    current_surface: str = "upload",
    behavior_mode: str = "",
    product_subintent: str = "",
    business_subintent: str = "",
    base_url: str = VIA_ACTIVITY_BASE_URL,
) -> dict[str, Any]:
    haystack = " ".join(
        [
            str(user_text or ""),
            str(title or ""),
            str(text or ""),
            str(product_subintent or ""),
            str(business_subintent or ""),
        ]
    ).strip().lower()
    surface = str(current_surface or "upload").strip().lower()
    mode = str(behavior_mode or "").strip().lower()
    product_intent = str(product_subintent or "").strip().lower()
    business_intent = str(business_subintent or "").strip().lower()

    activity_id = "page-patrol"
    if _contains_any(haystack, ("mini lf", "epic", "anamorphic", "t2.0", "pl mount")):
        activity_id = "mini-lf-epic"
    elif _contains_any(haystack, ("chapman", "dolly", "cart", "luna", "30-300", "42-420", "cine zoom", "试用", "借镜头", "借测", "租赁", "租镜头")) or business_intent in {
        "rental_partner",
        "borrow_trial",
        "trial_request",
    }:
        activity_id = "push-chapman-cart"
    elif _contains_any(haystack, ("z1", "z2", "flash", "ttl", "strobe", "lighting", "portrait light")):
        activity_id = "sony-z-flash"
    elif _contains_any(haystack, ("fujifilm", "fuji", "xf", "x mount", "street", "walkaround")):
        activity_id = "fujifilm-air-scout"
    elif _contains_any(haystack, ("sony air", "air", "travel", "student", "budget", "fe", "e mount")):
        activity_id = "sony-air-pack"
    elif _contains_any(haystack, ("lab", "135 lab", "35 lab", "flagship", "premium")):
        activity_id = "lab-portrait-bench"
    elif _contains_any(haystack, ("pro", "evo", "f1.4", "85mm f2.0", "everyday rig")):
        activity_id = "pro-evo-roam"
    elif _contains_any(haystack, ("shoot", "camera", "拍摄", "photo", "摄影", "creator", "content", "shot")):
        activity_id = "shoot-camera"
    elif surface in {"upload", "review"} or _contains_any(
        haystack,
        ("monitor", "review", "upload", "playback", "video", "analysis", "score", "critique"),
    ):
        activity_id = "monitor-review"
    elif mode == "photography":
        activity_id = "shoot-camera"
    elif mode == "gear" or product_intent in {"specs", "links", "comparison", "family_guide", "catalog"}:
        activity_id = "monitor-review"

    pack = build_via_activity_pack(base_url=base_url)
    activity = pack["activities"].get(activity_id) or pack["activities"][pack["default_activity_id"]]
    render_mode = str(activity.get("render_mode") or "canonical_pose").strip() or "canonical_pose"
    return {
        "version": pack["version"],
        "id": activity["id"],
        "label": activity["label"],
        "movement_mode": activity["movement_mode"],
        "render_mode": render_mode,
        "canonical_pose": str(activity.get("canonical_pose") or "front"),
        "prop_kit": str(activity.get("prop_kit") or "none"),
        "motion_clip": str(activity.get("motion_clip") or "idle-breathe"),
        "anchor_hint": str(activity.get("anchor_hint") or "page-grid"),
        "scene_line": activity["scene_line"],
        "ambient_lines": list(activity.get("ambient_lines") or []),
        "frame_count": 0 if render_mode == "canonical_pose" else int(activity.get("frame_count") or VIA_ACTIVITY_FRAME_COUNT),
        "frames": [] if render_mode == "canonical_pose" else list(activity.get("frames") or []),
        "sheet_url": "" if render_mode == "canonical_pose" else str(activity.get("sheet_url") or ""),
    }
