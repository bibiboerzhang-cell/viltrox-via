"""Viltrox product catalog and family metadata for Via product guidance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

from app.core.constants import PRODUCT_RULES

@dataclass(frozen=True)
class ViaProduct:
    label: str
    series: str
    format_tag: str
    mounts: tuple[str, ...]
    use_case: str
    use_case_zh: str
    budget_tier: str
    est_price_usd: int
    hero_reason: str
    hero_reason_zh: str
    official_url: str
    aliases: tuple[str, ...]

    @property
    def mount_label(self) -> str:
        return " / ".join(self.mounts)


def _official_search(label: str) -> str:
    return f"https://viltrox.com/search?q={quote_plus(label)}"


STORE_URL = "https://viltrox.com"

SERIES_OFFICIAL_URLS: dict[str, str] = {
    "CHIP": _official_search("AF 28mm F4.5 Chip Lens"),
    "AIR": _official_search("AIR Series"),
    "EVO": _official_search("EVO Series"),
    "PRO": _official_search("PRO Series"),
    "LAB": _official_search("LAB Series"),
    "EPIC": _official_search("EPIC Cinema Series"),
    "LUNA": _official_search("LUNA Cinema Zoom"),
    "LIGHT": _official_search("Vintage Z Series"),
}

FAMILY_TO_SERIES: dict[str, str] = {
    "pancake": "CHIP",
    "air": "AIR",
    "evo": "EVO",
    "pro": "PRO",
    "lab": "LAB",
    "epic": "EPIC",
    "luna": "LUNA",
    "lighting": "LIGHT",
}

SERIES_HIGHLIGHTS: dict[str, tuple[str, ...]] = {
    "AIR": (
        "AF 20mm F2.8 Air FF",
        "AF 40mm F2.5 Air FF",
        "AF 50mm F2.0 Air FF",
        "AF 56mm F1.7 Air APS-C",
    ),
    "EVO": (
        "AF 35mm F1.8 EVO APO",
        "AF 55mm F1.8 EVO APO",
        "AF 85mm F2.0 EVO",
    ),
    "PRO": (
        "AF 27mm F1.2 Pro APS-C",
        "AF 35mm F1.4 Pro FF",
        "AF 50mm F1.4 Pro FF",
        "AF 85mm F1.4 Pro FF",
    ),
    "LAB": (
        "AF 35mm F1.2 LAB",
        "AF 135mm F1.8 LAB",
    ),
    "EPIC": (
        "EPIC 25mm T2.0 1.33X",
        "EPIC 35mm T2.0 1.33X",
        "EPIC 50mm T2.0 1.33X",
        "EPIC 75mm T2.0 1.33X",
        "EPIC 100mm T2.0 1.33X",
        "EPIC 135mm T2.4 1.33X",
    ),
    "LUNA": (
        "LUNA 30-300mm T4.0",
        "LUNA 42-420mm T5.6",
    ),
    "LIGHT": (
        "Vintage Z1",
        "Vintage Z2 Mini",
    ),
}

MOUNT_LIBRARY: dict[str, dict[str, Any]] = {
    "sony_e": {
        "tokens": ("sony", "索尼", "sony e", "e-mount", "e mount", "fe", "a7", "fx3", "zv-e"),
        "label": "Sony E",
        "label_zh": "索尼 E 卡口",
    },
    "nikon_z": {
        "tokens": ("nikon", "尼康", "z mount", "z-mount", "z卡口"),
        "label": "Nikon Z",
        "label_zh": "尼康 Z 卡口",
    },
    "fuji_x": {
        "tokens": ("fujifilm", "fuji", "富士", "x mount", "x-mount", "x卡口"),
        "label": "Fujifilm X",
        "label_zh": "富士 X 卡口",
    },
    "canon_rf": {
        "tokens": ("canon", "佳能", "rf", "rf mount", "rf-mount"),
        "label": "Canon RF",
        "label_zh": "佳能 RF 卡口",
    },
}

SHORT_MOUNT_TOKENS: dict[str, str] = {
    "e": "sony_e",
    "z": "nikon_z",
    "x": "fuji_x",
    "rf": "canon_rf",
}


CATALOG: tuple[ViaProduct, ...] = (
    ViaProduct(
        label="AF 28mm F4.5 Chip Lens",
        series="CHIP",
        format_tag="Full-frame",
        mounts=("Sony E",),
        use_case="pancake street, ultra-light carry, everyday creator setups",
        use_case_zh="轻薄随身、街拍和轻量日常创作",
        budget_tier="budget",
        est_price_usd=99,
        hero_reason="it is the smallest Viltrox route when you want the most pocketable setup",
        hero_reason_zh="如果你想要最轻最薄的路线，它是唯卓仕最有辨识度的轻薄方案",
        official_url=_official_search("AF 28mm F4.5 Chip Lens"),
        aliases=("28mm", "28 4.5", "chip", "chip lens", "pancake 28", "饼干头", "薄饼"),
    ),
    ViaProduct(
        label="AF 14mm F4.0 Air FF",
        series="AIR",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="travel, architecture, gimbal moves, wide documentary coverage",
        use_case_zh="旅行、建筑、稳定器运动镜头和广角纪录片",
        budget_tier="budget",
        est_price_usd=189,
        hero_reason="it is the light ultra-wide Air choice when you want an affordable establishing lens",
        hero_reason_zh="如果你想要便宜又轻的超广角建立镜头，它是 Air 里的轻量路线",
        official_url=_official_search("AF 14mm F4.0 Air FF"),
        aliases=("14mm", "14 4", "air 14", "14 air", "viltrox 14"),
    ),
    ViaProduct(
        label="AF 20mm F2.8 Air FF",
        series="AIR",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="vlog, travel, gimbal, wide establishing shots",
        use_case_zh="vlog、旅行、稳定器和广角建立镜头",
        budget_tier="budget",
        est_price_usd=176,
        hero_reason="light, affordable, and easy to carry for student creators",
        hero_reason_zh="轻、便宜、好带，特别适合学生和轻装拍摄",
        official_url=_official_search("AF 20mm F2.8 Air FF"),
        aliases=("20mm", "20 2.8", "air 20", "viltrox 20", "20 air"),
    ),
    ViaProduct(
        label="AF 40mm F2.5 Air FF",
        series="AIR",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="street, daily carry, lifestyle, lightweight documentary",
        use_case_zh="街拍、日常随身、生活方式和轻纪录片",
        budget_tier="budget",
        est_price_usd=199,
        hero_reason="small everyday full-frame prime with a natural view",
        hero_reason_zh="是一支很适合每天随身带着拍的轻量全画幅镜头",
        official_url=_official_search("AF 40mm F2.5 Air FF"),
        aliases=("40mm", "40 2.5", "air 40", "viltrox 40", "40 air"),
    ),
    ViaProduct(
        label="AF 50mm F2.0 Air FF",
        series="AIR",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="portraits, interviews, campus filmmaking, daily creator use",
        use_case_zh="人像、访谈、校园创作和日常内容拍摄",
        budget_tier="budget",
        est_price_usd=199,
        hero_reason="the easiest low-cost 50mm entry for students and creators",
        hero_reason_zh="是学生和创作者最容易入手的低门槛 50mm",
        official_url="https://viltrox.com/pages/af-50-2-0-air-fe",
        aliases=("50mm", "50", "50 2", "50 f2", "50/2", "air 50", "50 air", "viltrox 50"),
    ),
    ViaProduct(
        label="AF 35mm F1.7 Air APS-C",
        series="AIR",
        format_tag="APS-C",
        mounts=("Sony E", "Nikon Z", "Fujifilm X"),
        use_case="APS-C daily carry, student storytelling, lightweight interviews",
        use_case_zh="APS-C 日常随身、学生叙事和轻量访谈",
        budget_tier="budget",
        est_price_usd=189,
        hero_reason="it is the easy all-round normal lens for crop creators who want a modern Air feel",
        hero_reason_zh="如果你是 APS-C 用户，想要更现代的 Air 风格标准焦段，它很顺手",
        official_url=_official_search("AF 35mm F1.7 Air APS-C"),
        aliases=("35 air", "air 35", "35 1.7 air", "viltrox 35 1.7"),
    ),
    ViaProduct(
        label="AF 56mm F1.7 Air APS-C",
        series="AIR",
        format_tag="APS-C",
        mounts=("Sony E", "Nikon Z", "Fujifilm X"),
        use_case="APS-C portraits, tighter talking-heads, student creator kits",
        use_case_zh="APS-C 人像、半身访谈和学生创作者套装",
        budget_tier="budget",
        est_price_usd=179,
        hero_reason="excellent portrait value if you are on APS-C and cost-sensitive",
        hero_reason_zh="如果你是 APS-C 机身又很看重性价比，它很适合做人像主力",
        official_url=_official_search("AF 56mm F1.7 Air APS-C"),
        aliases=("56mm", "56 1.7", "56 air", "air 56", "viltrox 56 1.7"),
    ),
    ViaProduct(
        label="AF 35mm F1.8 EVO APO",
        series="EVO",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="clean everyday cinema, hybrid photo-video, refined color and APO rendering",
        use_case_zh="干净的日常电影感、混合拍摄和更克制的 APO 成像",
        budget_tier="mid",
        est_price_usd=399,
        hero_reason="it is the more modern everyday full-frame lane when you want an EVO look instead of an older classic",
        hero_reason_zh="如果你想要更现代的 EVO 画风，而不是旧一点的经典路线，它是更顺的起点",
        official_url=_official_search("AF 35mm F1.8 EVO APO"),
        aliases=("35 evo", "evo 35", "35 1.8 evo", "evo apo 35"),
    ),
    ViaProduct(
        label="AF 55mm F1.8 EVO APO",
        series="EVO",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="portrait-story hybrid, product detail, polished full-frame creator work",
        use_case_zh="人像叙事混合拍摄、产品细节和更精致的全画幅创作",
        budget_tier="mid",
        est_price_usd=459,
        hero_reason="it gives you the more polished normal-tele look in the EVO family",
        hero_reason_zh="在 EVO 里，它更像精致正常偏长焦的主力位",
        official_url=_official_search("AF 55mm F1.8 EVO APO"),
        aliases=("55 evo", "evo 55", "55 1.8 evo", "evo apo 55"),
    ),
    ViaProduct(
        label="AF 85mm F2.0 EVO",
        series="EVO",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="modern portraits, beauty, compressed lifestyle and cleaner tele framing",
        use_case_zh="现代人像、妆造、压缩感生活方式和更干净的中长焦画面",
        budget_tier="mid",
        est_price_usd=499,
        hero_reason="it is the newer 85 direction if you want an EVO portrait look instead of older FE II energy",
        hero_reason_zh="如果你想走更新的 EVO 人像路线，而不是旧的 FE II 味道，它会更贴近你的方向",
        official_url=_official_search("AF 85mm F2.0 EVO"),
        aliases=("85 evo", "evo 85", "85 2.0 evo", "85 f2 evo"),
    ),
    ViaProduct(
        label="AF 27mm F1.2 Pro APS-C",
        series="PRO",
        format_tag="APS-C",
        mounts=("Sony E", "Nikon Z", "Fujifilm X"),
        use_case="APS-C hybrid video, environmental portraits, low-light creator work",
        use_case_zh="APS-C 混合视频、环境人像和低光创作",
        budget_tier="mid",
        est_price_usd=549,
        hero_reason="fast, premium APS-C option when you need more punch and shallow depth",
        hero_reason_zh="如果你想要更强进光和更浅景深，这支会更有冲击力",
        official_url=_official_search("AF 27mm F1.2 Pro APS-C"),
        aliases=("27mm", "27 1.2", "27 pro", "pro 27", "viltrox 27"),
    ),
    ViaProduct(
        label="AF 35mm F1.4 Pro FF",
        series="PRO",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="cinematic full-frame normal wide, low light editorial, richer hybrid visuals",
        use_case_zh="电影感全画幅偏广标准、低光编辑感画面和更厚的混合创作",
        budget_tier="mid",
        est_price_usd=599,
        hero_reason="it is the more cinematic wide-normal pro route when 50mm feels too tight",
        hero_reason_zh="如果你觉得 50mm 稍紧，这支更像电影化的偏广标准主力",
        official_url=_official_search("AF 35mm F1.4 Pro FF"),
        aliases=("35 pro", "pro 35", "35 1.4 pro", "viltrox 35 pro"),
    ),
    ViaProduct(
        label="AF 50mm F1.4 Pro FF",
        series="PRO",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="cinematic portraits, low light, richer subject isolation",
        use_case_zh="氛围感人像、夜景和更强的主体分离",
        budget_tier="mid",
        est_price_usd=599,
        hero_reason="the stronger 50mm when you want more separation and night performance",
        hero_reason_zh="如果你想要更强虚化和夜景表现，这支 50 更上一个档位",
        official_url=_official_search("AF 50mm F1.4 Pro FF"),
        aliases=("50 1.4", "50 pro", "50 f1.4", "pro 50", "50mm pro", "viltrox 50 pro"),
    ),
    ViaProduct(
        label="AF 85mm F1.8 FE II",
        series="LENS",
        format_tag="Full-frame",
        mounts=("Sony E",),
        use_case="portrait, compression, beauty and tighter interview coverage",
        use_case_zh="人像、压缩感画面、妆造和更紧的访谈画面",
        budget_tier="mid",
        est_price_usd=399,
        hero_reason="a classic portrait step-up when you are already sure you want 85mm on Sony E",
        hero_reason_zh="如果你已经确定要走索尼 E 的 85mm 人像路线，它会是更经典的向上升级",
        official_url=_official_search("AF 85mm F1.8 FE II"),
        aliases=("85mm", "85 1.8", "85 fe ii", "85 ii", "viltrox 85 1.8"),
    ),
    ViaProduct(
        label="AF 85mm F1.4 Pro FF",
        series="PRO",
        format_tag="Full-frame",
        mounts=("Nikon Z",),
        use_case="hero portraits, beauty, compressed cinematic shots",
        use_case_zh="主视觉人像、妆造和更有压缩感的电影镜头",
        budget_tier="pro",
        est_price_usd=899,
        hero_reason="portrait specialist for creators who want premium compression and blur",
        hero_reason_zh="是想要高阶人像压缩感和虚化的创作者向上选择",
        official_url=_official_search("AF 85mm F1.4 Pro FF"),
        aliases=("85 pro", "pro 85", "85 1.4 pro", "viltrox 85 pro"),
    ),
    ViaProduct(
        label="AF 35mm F1.2 LAB",
        series="LAB",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="premium hero work, night scenes, flagship portrait-story frames",
        use_case_zh="旗舰级主视觉、夜景和高阶人像叙事",
        budget_tier="pro",
        est_price_usd=999,
        hero_reason="it is the flagship full-frame statement piece when you want LAB-level rendering",
        hero_reason_zh="如果你要的是 LAB 级别的旗舰成像，它就是更有标志性的主力镜头",
        official_url=_official_search("AF 35mm F1.2 LAB"),
        aliases=("35 lab", "lab 35", "35 1.2 lab"),
    ),
    ViaProduct(
        label="AF 135mm F1.8 LAB",
        series="LAB",
        format_tag="Full-frame",
        mounts=("Sony E", "Nikon Z"),
        use_case="hero portraits, compression, fashion, premium cinematic close-ups",
        use_case_zh="主视觉人像、压缩感画面、时尚和高阶电影特写",
        budget_tier="pro",
        est_price_usd=899,
        hero_reason="it is the LAB tele route when you want premium compression and cleaner isolation",
        hero_reason_zh="如果你想要 LAB 系列里更高级的长焦压缩感，它就是更像旗舰长焦的方案",
        official_url=_official_search("AF 135mm F1.8 LAB"),
        aliases=("135 lab", "lab 135", "135 1.8 lab"),
    ),
    ViaProduct(
        label="EPIC Cinema Series",
        series="EPIC",
        format_tag="Cinema",
        mounts=("PL / cinema workflow",),
        use_case="anamorphic language, cinema rigs, narrative feature and commercial production",
        use_case_zh="变形宽银幕语言、电影机 rig、剧情片和商业片制作",
        budget_tier="cine",
        est_price_usd=0,
        hero_reason="it is the dedicated anamorphic cinema lane when you want a true EPIC look",
        hero_reason_zh="如果你想要真正的 EPIC 变形电影感，它就是独立的电影镜头路线",
        official_url=SERIES_OFFICIAL_URLS["EPIC"],
        aliases=("epic", "epic series", "epic cinema", "anamorphic", "1.33x", "变形", "电影镜头", "blue streak", "silver flare", "maestro", "memento", "pl"),
    ),
    ViaProduct(
        label="LUNA 30-300mm T4.0",
        series="LUNA",
        format_tag="Cinema zoom",
        mounts=("Cinema zoom workflow",),
        use_case="long zoom coverage, sports, wildlife, documentary and broadcast scale cinema work",
        use_case_zh="长焦变焦覆盖、体育、野生、纪录片和广电级电影工作流",
        budget_tier="cine",
        est_price_usd=0,
        hero_reason="it is the more versatile Luna zoom when you want one long lens to cover the ride",
        hero_reason_zh="如果你想用一支长焦电影变焦覆盖更多场景，它是更灵活的 Luna 路线",
        official_url=_official_search("LUNA 30-300mm T4.0"),
        aliases=("luna 30-300", "30-300", "30 300", "luna t4", "luna", "cine zoom", "broadcast zoom", "10x zoom", "lpl"),
    ),
    ViaProduct(
        label="LUNA 42-420mm T5.6",
        series="LUNA",
        format_tag="Cinema zoom",
        mounts=("Cinema zoom workflow",),
        use_case="extreme long-lens cinema, remote heads, stadium and wildlife coverage",
        use_case_zh="超长焦电影工作流、遥控云台、体育场和野生题材覆盖",
        budget_tier="cine",
        est_price_usd=0,
        hero_reason="it is the giant Luna option when you need extreme reach and a true long-lens statement",
        hero_reason_zh="如果你需要真正夸张的超长焦覆盖，它就是更有标志性的 Luna 巨炮路线",
        official_url=_official_search("LUNA 42-420mm T5.6"),
        aliases=("luna 42-420", "42-420", "42 420", "420", "luna t5.6", "large format zoom", "sports zoom", "wildlife zoom"),
    ),
    ViaProduct(
        label="Vintage Z1",
        series="LIGHT",
        format_tag="Flash",
        mounts=("Lighting accessory",),
        use_case="retro flash portraits, event pops, quick on-camera light and mood accents",
        use_case_zh="复古闪光人像、活动抓拍、机顶补光和氛围点亮",
        budget_tier="gear",
        est_price_usd=179,
        hero_reason="it is the flagship retro-flash vibe if you want a more characterful on-camera light",
        hero_reason_zh="如果你想要更有性格的复古闪光灯气质，它是唯卓仕这条线的代表",
        official_url=_official_search("Vintage Z1"),
        aliases=("z1", "vintage z1", "flash z1", "闪光灯 z1", "复古 z1"),
    ),
    ViaProduct(
        label="Vintage Z2 Mini",
        series="LIGHT",
        format_tag="Flash",
        mounts=("Lighting accessory",),
        use_case="small creator kits, lightweight event carry, simple fill and pocket flash support",
        use_case_zh="轻量创作者套装、活动随身、简易补光和口袋闪光支持",
        budget_tier="gear",
        est_price_usd=119,
        hero_reason="it is the easiest small-flash entry if you want the Viltrox vintage look for less",
        hero_reason_zh="如果你想用更低门槛进入唯卓仕复古闪光灯路线，它是更轻更容易入手的选择",
        official_url=_official_search("Vintage Z2 Mini"),
        aliases=("z2", "z2 mini", "vintage z2", "flash z2", "闪光灯 z2", "复古 z2"),
    ),
)

FAMILY_GUIDES: dict[str, dict[str, Any]] = {
    "air": {
        "zh_title": "Air 系列",
        "zh_text": f"如果你在看唯卓仕 Air 系列，它的核心就是轻、小、适合日常创作。常见方向是 AF 20mm F2.8 Air FF、AF 40mm F2.5 Air FF、AF 50mm F2.0 Air FF，还有 APS-C 的 AF 56mm F1.7 Air。官方总入口在 {SERIES_OFFICIAL_URLS['AIR']}",
        "en_title": "Air line",
        "en_text": f"If you mean the Viltrox Air line, the whole point is light, compact, and creator-friendly glass. Start with AF 20mm F2.8 Air FF, AF 40mm F2.5 Air FF, AF 50mm F2.0 Air FF, or the APS-C AF 56mm F1.7 Air. Store: {SERIES_OFFICIAL_URLS['AIR']}",
        "quick_actions_zh": ["给我 50 Air", "给我 40 Air", "按预算选"],
        "quick_actions_en": ["Show 50 Air", "Show 40 Air", "Budget picks"],
    },
    "pancake": {
        "zh_title": "饼干头方向",
        "zh_text": f"如果你想要唯卓仕偏饼干头/轻薄路线，我会先让你看 AF 28mm F4.5 Chip Lens，再看 AF 40mm F2.5 Air FF。这两条更适合轻便随身和街拍。官网入口：{STORE_URL}",
        "en_title": "Pancake direction",
        "en_text": f"If you want the Viltrox pancake / ultra-light route, start with the AF 28mm F4.5 Chip Lens and then the AF 40mm F2.5 Air FF. Both fit compact everyday carry. Store: {STORE_URL}",
        "quick_actions_zh": ["讲 28mm", "讲 40mm", "给我链接"],
        "quick_actions_en": ["Show 28mm", "Show 40mm", "Give links"],
    },
    "evo": {
        "zh_title": "EVO 系列",
        "zh_text": f"如果你说的是唯卓仕 EVO，我会先让你看 AF 35mm F1.8 EVO APO、AF 55mm F1.8 EVO APO 和 AF 85mm F2.0 EVO。它更像现代、干净、克制的全画幅路线。官方入口：{SERIES_OFFICIAL_URLS['EVO']}",
        "en_title": "EVO line",
        "en_text": f"If you mean the Viltrox EVO line, start with AF 35mm F1.8 EVO APO, AF 55mm F1.8 EVO APO, and AF 85mm F2.0 EVO. This is the cleaner, more modern full-frame lane. Store: {SERIES_OFFICIAL_URLS['EVO']}",
        "quick_actions_zh": ["讲 35 EVO", "讲 55 EVO", "讲 85 EVO"],
        "quick_actions_en": ["Show 35 EVO", "Show 55 EVO", "Show 85 EVO"],
    },
    "lab": {
        "zh_title": "LAB 系列",
        "zh_text": f"如果你在看唯卓仕 LAB，它就是旗舰路线。我会先让你看 AF 35mm F1.2 LAB 和 AF 135mm F1.8 LAB。适合更高阶的人像、夜景和主视觉。官网入口：{SERIES_OFFICIAL_URLS['LAB']}",
        "en_title": "LAB line",
        "en_text": f"If you mean Viltrox LAB, that is the flagship lane. Start with AF 35mm F1.2 LAB and AF 135mm F1.8 LAB for higher-end portraits, hero frames, and night work. Store: {SERIES_OFFICIAL_URLS['LAB']}",
        "quick_actions_zh": ["讲 35 LAB", "讲 135 LAB", "值得买吗"],
        "quick_actions_en": ["Show 35 LAB", "Show 135 LAB", "Is LAB worth it?"],
    },
    "pro": {
        "zh_title": "Pro 系列",
        "zh_text": f"唯卓仕 Pro 更偏向高性价比的高性能路线。常见入口是 AF 35mm F1.4 Pro FF、AF 50mm F1.4 Pro FF、AF 85mm F1.4 Pro FF，以及 APS-C 的 27/56/75 Pro。官网入口：{SERIES_OFFICIAL_URLS['PRO']}",
        "en_title": "Pro line",
        "en_text": f"The Viltrox Pro line is the high-performance lane before you jump all the way to LAB or cinema gear. Start with AF 35mm F1.4 Pro FF, AF 50mm F1.4 Pro FF, AF 85mm F1.4 Pro FF, or APS-C Pro lenses. Store: {SERIES_OFFICIAL_URLS['PRO']}",
        "quick_actions_zh": ["讲 35 Pro", "讲 50 Pro", "讲 85 Pro"],
        "quick_actions_en": ["Show 35 Pro", "Show 50 Pro", "Show 85 Pro"],
    },
    "epic": {
        "zh_title": "EPIC 电影系列",
        "zh_text": f"如果你说的是唯卓仕 EPIC，那是 1.33X 变形宽银幕电影线，常见焦段从 18 / 21 / 25 / 29 / 35 / 40 / 50 / 65 Macro / 75 / 100 / 135 / 150 / 180mm 展开，更适合电影机、商业片和剧情片工作流。先从 EPIC Cinema Series 开始看，再按焦段挑。官网入口：{SERIES_OFFICIAL_URLS['EPIC']}",
        "en_title": "EPIC cinema line",
        "en_text": f"If you mean Viltrox EPIC, that is the 1.33X anamorphic cinema line for narrative, commercial, and large-rig production, spanning 18 / 21 / 25 / 29 / 35 / 40 / 50 / 65 Macro / 75 / 100 / 135 / 150 / 180mm. Start with EPIC Cinema Series and then narrow by focal length. Store: {SERIES_OFFICIAL_URLS['EPIC']}",
        "quick_actions_zh": ["EPIC 有哪些焦段", "讲变形风格", "给我官网"],
        "quick_actions_en": ["Show EPIC focal lengths", "Explain the anamorphic look", "Give store link"],
    },
    "luna": {
        "zh_title": "LUNA 电影变焦",
        "zh_text": f"如果你说的是唯卓仕 LUNA，它现在的代表是 LUNA 30-300mm T4.0 和 LUNA 42-420mm T5.6。这是更偏电影、体育、纪录和广电级超长焦工作流的路线。官网入口：{SERIES_OFFICIAL_URLS['LUNA']}",
        "en_title": "LUNA cine zooms",
        "en_text": f"If you mean Viltrox LUNA, the headline zooms are LUNA 30-300mm T4.0 and LUNA 42-420mm T5.6. This is the cinema zoom route for documentary, sports, wildlife, and broadcast scale work. Store: {SERIES_OFFICIAL_URLS['LUNA']}",
        "quick_actions_zh": ["讲 30-300", "讲 42-420", "适合什么项目"],
        "quick_actions_en": ["Show 30-300", "Show 42-420", "Best use cases"],
    },
    "lighting": {
        "zh_title": "Z 系列闪光灯",
        "zh_text": f"如果你在问唯卓仕闪光灯，我会先让你看 Vintage Z1 和 Vintage Z2 Mini。Z1 更像主力复古闪光灯，Z2 Mini 更轻更适合随身。官网入口：{SERIES_OFFICIAL_URLS['LIGHT']}",
        "en_title": "Z flash line",
        "en_text": f"If you mean Viltrox flashes, start with Vintage Z1 and Vintage Z2 Mini. Z1 is the more flagship retro-flash vibe, while Z2 Mini is the smaller easy-carry option. Store: {SERIES_OFFICIAL_URLS['LIGHT']}",
        "quick_actions_zh": ["讲 Z1", "讲 Z2", "适合人像吗"],
        "quick_actions_en": ["Show Z1", "Show Z2", "Good for portraits?"],
    },
}


GENERIC_CATALOG: tuple[dict[str, str], ...] = tuple(
    {
        "label": str(item.get("label") or "").strip(),
        "series": str(item.get("series") or "").strip(),
        "official_url": _official_search(str(item.get("label") or "").strip()),
    }
    for item in PRODUCT_RULES
    if str(item.get("label") or "").strip()
)


