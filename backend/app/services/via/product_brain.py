"""
services/via/product_brain.py — deterministic Viltrox-first product guidance for Via
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus
import re

from app.core.constants import PRODUCT_RULES
from app.core.logging import get_logger
from app.services.intelligence.bh_repository import get_latest_bh_products
from app.services.via.external_viltrox_assets import handle_external_competitor_query

logger = get_logger(__name__)


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


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def detect_reply_language(text: str) -> str:
    return "zh" if _contains_cjk(text) else "en"


def _detect_reply_language(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str:
    if _contains_cjk(text):
        return "zh"
    blob = " ".join(
        [
            str(profile_context or ""),
            str((session_state or {}).get("last_user_language") or ""),
            str((session_state or {}).get("preferred_language") or ""),
        ]
    )
    if _contains_cjk(blob) or "zh" in _lower(blob):
        return "zh"
    return "en"


def _lower(text: str) -> str:
    return str(text or "").strip().lower()


def _has_any(text: str, tokens: tuple[str, ...] | list[str]) -> bool:
    lowered = _lower(text)
    return any(token in lowered for token in tokens)


def _budget_query(text: str) -> bool:
    return _has_any(text, ("student", "budget", "cheap", "affordable", "低预算", "预算不高", "学生", "便宜", "性价比", "刀", "美金"))


def _apsc_query(text: str) -> bool:
    return _has_any(text, ("aps-c", "apsc", "crop", "半画幅", "富士", "fujifilm", "x mount", "x-mount"))


def _spec_query(text: str) -> bool:
    return _has_any(text, ("spec", "specs", "parameter", "parameters", "参数", "规格", "配置", "多少光圈", "卡口", "mount"))


def _link_query(text: str) -> bool:
    return _has_any(text, ("link", "url", "链接", "官网", "购买", "site", "store"))


def _comparison_query(text: str) -> bool:
    return _has_any(text, ("compare", "vs", "versus", "区别", "对比", "哪个好", "怎么选", "差别", "difference"))


def _family_guide_query(text: str, family: str | None) -> bool:
    if not family:
        return False
    lowered = _lower(text)
    if _comparison_query(lowered) or _spec_query(lowered) or _link_query(lowered) or _specific_product_prompt(lowered):
        return False
    guide_terms = (
        "系列",
        "产品线",
        "系列里",
        "series",
        "line",
        "lineup",
        "catalog",
        "路线",
        "讲讲",
        "介绍",
        "什么意思",
        "是什么",
        "有哪些",
        "都有谁",
        "怎么理解",
        "梳理",
    )
    return any(term in lowered for term in guide_terms)


def _specific_product_prompt(text: str) -> bool:
    lowered = _lower(text)
    if re.search(r"(30-300|42-420|14mm|20mm|27mm|28mm|35mm|40mm|50mm|55mm|56mm|85mm|135mm)", lowered):
        return True
    if re.search(r"(?<!\\d)(14|20|27|28|35|40|42|50|55|56|85|135)(?!\\d)", lowered):
        return True
    return _has_any(
        lowered,
        (
            "chip lens",
            "fe ii",
            "z1",
            "z2",
            "30-300",
            "42-420",
            "85 evo",
            "35 evo",
            "55 evo",
            "50 air",
            "40 air",
            "20 air",
            "35 lab",
            "135 lab",
            "35 pro",
            "50 pro",
            "85 pro",
        ),
    )


def _scenario_label(user_text: str, lang: str) -> str:
    lowered = _lower(user_text)
    if _has_any(lowered, ("portrait", "人像", "beauty", "妆造")):
        return "人像和访谈" if lang == "zh" else "portraits and talking-head work"
    if _has_any(lowered, ("street", "街拍", "travel", "旅行", "daily", "日常", "campus", "学生")):
        return "轻便随拍和校园日常" if lang == "zh" else "light everyday carry and campus creator work"
    if _has_any(lowered, ("cinema", "电影", "filmmaking", "narrative", "commercial", "剧情", "广告")):
        return "更偏电影和商业拍摄" if lang == "zh" else "cinema and commercial production"
    if _has_any(lowered, ("vlog", "travel", "旅行", "gimbal", "稳定器")):
        return "vlog、旅行和稳定器镜头" if lang == "zh" else "vlog, travel, and gimbal work"
    if _has_any(lowered, ("product", "开箱", "detail", "细节")):
        return "产品和细节拍摄" if lang == "zh" else "product and detail work"
    return "你现在这个拍摄场景" if lang == "zh" else "your current shooting scenario"


def _normalize_market_text(text: str) -> str:
    lowered = _lower(text)
    lowered = lowered.replace("full frame", "ff").replace("full-frame", "ff")
    lowered = lowered.replace("sony e", "sony").replace("nikon z", "nikon").replace("fujifilm x", "fuji")
    return re.sub(r"[^a-z0-9\+\./-]+", " ", lowered).strip()


def _product_match_tokens(product: ViaProduct) -> set[str]:
    ignored = {
        "af", "lens", "series", "full", "frame", "ff", "apo", "mini", "cinema", "workflow",
        "viltrox", "vintage", "lighting", "accessory", "for", "the",
    }
    tokens: set[str] = set()
    for source in (product.label, product.series, *product.aliases):
        normalized = _normalize_market_text(source)
        if not normalized:
            continue
        if " " in normalized and len(normalized) >= 5:
            tokens.add(normalized)
        for part in normalized.split():
            if part in ignored:
                continue
            if len(part) >= 3 or part.endswith("mm") or part.startswith("f"):
                tokens.add(part)
    return tokens


def _bh_score(product: ViaProduct, row: dict[str, Any]) -> int:
    title = _normalize_market_text(str(row.get("title") or ""))
    if not title:
        return 0
    score = 0
    full_label = _normalize_market_text(product.label)
    if full_label and full_label in title:
        score += 10
    for token in _product_match_tokens(product):
        if token and token in title:
            score += 2
    if product.series and product.series.lower() in title:
        score += 2
    if any(mount.lower().split()[0] in title for mount in product.mounts if mount and mount[0].isalpha()):
        score += 1
    return score


def _bh_market_rows(products: list[ViaProduct]) -> dict[str, dict[str, Any]]:
    rows = get_latest_bh_products(limit=120)
    matched: dict[str, dict[str, Any]] = {}
    for product in products:
        best_row: dict[str, Any] | None = None
        best_score = 0
        for row in rows:
            score = _bh_score(product, row)
            if score > best_score:
                best_score = score
                best_row = row
        if best_row and best_score >= 4:
            matched[product.label] = best_row
    return matched


def _market_line(product: ViaProduct, market_row: dict[str, Any] | None, lang: str) -> str:
    if not market_row:
        return (
            f"官方商城入口是 {product.official_url}，如果你想要我继续比一比，我会沿着 {product.series} 这条线帮你挑。"
            if lang == "zh"
            else f"The official store entry is {product.official_url}. If you want, I can keep comparing inside the {product.series} lane."
        )
    price = float(market_row.get("price") or 0)
    rating = float(market_row.get("rating") or 0)
    review_count = int(market_row.get("review_count") or 0)
    in_stock = bool(market_row.get("in_stock"))
    stock_word = "有现货" if in_stock else "暂时缺货"
    stock_word_en = "in stock" if in_stock else "currently out of stock"
    if lang == "zh":
        bits = [f"B&H 观察里这支目前 {stock_word}"]
        if price > 0:
            bits.append(f"价格约 ${price:.0f}")
        if rating > 0:
            bits.append(f"评分 {rating:.1f}")
        if review_count > 0:
            bits.append(f"{review_count} 条评论")
        bits.append(f"官方商城：{product.official_url}")
        return "，".join(bits) + "。"
    bits = [f"B&H currently shows it {stock_word_en}"]
    if price > 0:
        bits.append(f"around ${price:.0f}")
    if rating > 0:
        bits.append(f"rated {rating:.1f}")
    if review_count > 0:
        bits.append(f"with {review_count} reviews")
    bits.append(f"official store: {product.official_url}")
    return ", ".join(bits) + "."


def _compare_axis(lead: ViaProduct, alt: ViaProduct, lang: str) -> str:
    if lang == "zh":
        return (
            f"{lead.label} 更偏 {lead.use_case_zh}，{alt.label} 更偏 {alt.use_case_zh}。"
            if lead.label != alt.label
            else f"{lead.label} 更像 {lead.use_case_zh} 这条路线。"
        )
    return (
        f"{lead.label} leans toward {lead.use_case}, while {alt.label} leans toward {alt.use_case}."
        if lead.label != alt.label
        else f"{lead.label} is the stronger fit for {lead.use_case}."
    )


def _profile_blob(profile_context: str | None, session_state: dict[str, Any] | None) -> str:
    bits = [str(profile_context or "")]
    if session_state:
        bits.extend(
            [
                " ".join(session_state.get("last_product_labels") or []),
                str(session_state.get("last_mount_hint") or ""),
                str(session_state.get("last_budget_hint") or ""),
                str(session_state.get("last_product_series") or ""),
                str(session_state.get("last_product_summary") or ""),
            ]
        )
    return _lower(" ".join(bit for bit in bits if bit))


def _extract_budget(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> int | None:
    lowered = _lower(text)
    patterns = (
        r"(?:预算|budget|under|below|around|about|就|only|学生)\s*\$?(\d{2,4})",
        r"\$?(\d{2,4})\s*(?:usd|刀|美金|dollars?)",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            try:
                value = int(match.group(1))
                if 50 <= value <= 5000:
                    return value
            except Exception:
                logger.warning(
                    "via.product_brain.budget_match_parse_failed",
                    extra={"text": text[:120]},
                    exc_info=True,
                )
    session_value = None
    if session_state:
        try:
            session_value = int(session_state.get("last_budget_hint") or 0)
        except Exception:
            session_value = None
    if session_value:
        return session_value
    blob = _profile_blob(profile_context, session_state)
    if _budget_query(blob):
        return 300
    return None


def _family_key(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str | None:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    if any(token in lowered for token in ("饼干头", "pancake", "chip lens", "chip", "薄饼")):
        return "pancake"
    if any(token in lowered for token in ("air", "airy", "轻便", "air系", "air 系", "air ff", "air aps-c", "travel prime")) or ("air" in blob and _has_any(lowered or blob, ("50", "40", "20", "air"))):
        return "air"
    if any(token in lowered for token in ("evo", "evo apo", "evo 系", "evo 系列")):
        return "evo"
    if any(token in lowered for token in ("lab", "旗舰", "lab line")):
        return "lab"
    if any(token in lowered for token in ("pro", "pro ff", "pro aps-c", "pro 系", "pro 系列")):
        return "pro"
    if any(token in lowered for token in ("epic", "anamorphic", "1.33x", "变形", "电影镜头", "blue streak", "silver flare", "宽银幕", "pl mount", "pl卡口", "maestro", "memento", "squeeze")):
        return "epic"
    if any(token in lowered for token in ("luna", "30-300", "42-420", "cine zoom", "broadcast", "lpl", "10x zoom", "large format zoom", "体育转播")):
        return "luna"
    if any(token in lowered for token in ("z1", "z2", "flash", "strobe", "speedlite", "闪光灯", "灯", "vintage z", "ttl", "hotshoe flash")):
        return "lighting"
    return None


def _series_rule_matches(family: str | None, text: str, limit: int = 4) -> list[dict[str, str]]:
    series = FAMILY_TO_SERIES.get(str(family or ""))
    if not series:
        return []
    lowered = _lower(text)
    matches: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in PRODUCT_RULES:
        item_series = str(item.get("series") or "").strip().upper()
        label = str(item.get("label") or "").strip()
        if item_series != series or not label:
            continue
        keywords = [str(keyword).strip().lower() for keyword in item.get("keywords") or [] if str(keyword).strip()]
        hit = bool(lowered) and (label.lower() in lowered or any(keyword in lowered for keyword in keywords[:16]))
        if hit and label not in seen:
            matches.append({"label": label, "series": series, "official_url": _official_search(label)})
            seen.add(label)
        if len(matches) >= limit:
            return matches
    for label in SERIES_HIGHLIGHTS.get(series, ()):
        if label in seen:
            continue
        matches.append({"label": label, "series": series, "official_url": _official_search(label)})
        if len(matches) >= limit:
            break
    return matches


def _series_selection_line(family: str | None, user_text: str, lang: str, *, limit: int = 4) -> str:
    matches = _series_rule_matches(family, user_text, limit=limit)
    series = FAMILY_TO_SERIES.get(str(family or ""))
    if not matches or not series:
        return ""
    labels = "、".join(item["label"] for item in matches) if lang == "zh" else ", ".join(item["label"] for item in matches)
    url = SERIES_OFFICIAL_URLS.get(series, STORE_URL)
    if lang == "zh":
        return f"这条线我会先看 {labels}。系列官方入口：{url}"
    return f"I would start with {labels}. Official series entry: {url}"


def _series_rule_guide_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    guide = FAMILY_GUIDES[family]
    detail_line = _series_selection_line(family, user_text, lang, limit=6)
    text = (guide["zh_text"] if lang == "zh" else guide["en_text"]).strip()
    if detail_line:
        text = f"{text} {detail_line}".strip()
    return {
        "title": guide["zh_title"] if lang == "zh" else guide["en_title"],
        "text": text,
        "quick_actions": guide["quick_actions_zh"] if lang == "zh" else guide["quick_actions_en"],
        "lock_ai_override": False,
        "product_subintent": "family_guide",
        "behavior_mode": _product_behavior_mode("family_guide"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_links_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    matches = _series_rule_matches(family, user_text, limit=3)
    series = FAMILY_TO_SERIES.get(family, "")
    series_url = SERIES_OFFICIAL_URLS.get(series, STORE_URL)
    listing = " / ".join(f"{item['label']}：{item['official_url']}" for item in matches) if lang == "zh" else " / ".join(f"{item['label']}: {item['official_url']}" for item in matches)
    text = (
        f"我先只给你唯卓仕官方入口。你可以先看：{listing}。系列总入口：{series_url}。"
        if lang == "zh"
        else f"I will keep this Viltrox-first. Start here: {listing}. Series entry: {series_url}."
    )
    return {
        "title": "官方链接" if lang == "zh" else "Official links",
        "text": text,
        "quick_actions": ["讲参数", "对比一下", "给我官网"] if lang == "zh" else ["Show specs", "Compare them", "Series page"],
        "lock_ai_override": True,
        "product_subintent": "links",
        "behavior_mode": _product_behavior_mode("links"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_specs_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    detail_line = _series_selection_line(family, user_text, lang, limit=4)
    series = FAMILY_TO_SERIES.get(family, "")
    if lang == "zh":
        note = (
            "EPIC 这条线主打 1.33X 变形、PL 电影工作流，65mm 是 Macro 例外。"
            if series == "EPIC"
            else "LUNA 这条线是超长焦 10x 电影变焦，偏体育、纪录和广电工作流。"
        )
        text = f"我先按唯卓仕官方系列讲：{note} {detail_line}".strip()
    else:
        note = (
            "EPIC is the 1.33X anamorphic PL-mount cinema lane, with the 65mm as the Macro outlier."
            if series == "EPIC"
            else "LUNA is the long-reach 10x cinema zoom lane for sports, documentary, and broadcast-scale workflows."
        )
        text = f"I will frame it from the official Viltrox family first: {note} {detail_line}".strip()
    return {
        "title": "唯卓仕参数" if lang == "zh" else "Viltrox specs",
        "text": text,
        "quick_actions": ["给我链接", "对比一下", "给我官网"] if lang == "zh" else ["Give links", "Compare them", "Series page"],
        "lock_ai_override": True,
        "product_subintent": "specs",
        "behavior_mode": _product_behavior_mode("specs"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _series_rule_comparison_reply(family: str, lang: str, user_text: str, mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    matches = _series_rule_matches(family, user_text, limit=2)
    lead = matches[0]
    alt = matches[1] if len(matches) > 1 else matches[0]
    lead_focal = int(re.search(r"(\d{2,3})mm", lead["label"]).group(1)) if re.search(r"(\d{2,3})mm", lead["label"]) else 0
    alt_focal = int(re.search(r"(\d{2,3})mm", alt["label"]).group(1)) if re.search(r"(\d{2,3})mm", alt["label"]) else 0
    series_url = SERIES_OFFICIAL_URLS.get(FAMILY_TO_SERIES.get(family, ""), STORE_URL)
    if lang == "zh":
        lead_desc = "更偏广角建立和环境叙事" if lead_focal and alt_focal and lead_focal < alt_focal else "更偏长焦压缩和主体特写"
        alt_desc = "更偏广角建立和环境叙事" if lead_focal and alt_focal and alt_focal < lead_focal else "更偏长焦压缩和主体特写"
        text = (
            f"我会这样看：{lead['label']} {lead_desc}，{alt['label']} {alt_desc}。"
            f" 它们都属于唯卓仕 {FAMILY_TO_SERIES.get(family, family).upper()} 系列，系列入口：{series_url}。"
        )
    else:
        lead_desc = "leans wider for establishing and environmental storytelling" if lead_focal and alt_focal and lead_focal < alt_focal else "leans longer for compression and tighter hero shots"
        alt_desc = "leans wider for establishing and environmental storytelling" if lead_focal and alt_focal and alt_focal < lead_focal else "leans longer for compression and tighter hero shots"
        text = (
            f"I would split it like this: {lead['label']} {lead_desc}, while {alt['label']} {alt_desc}. "
            f"Both sit inside the Viltrox {FAMILY_TO_SERIES.get(family, family).upper()} family. Series entry: {series_url}."
        )
    return {
        "title": "怎么选" if lang == "zh" else "How I would choose",
        "text": text,
        "quick_actions": ["给我链接", "讲参数", "给我官网"] if lang == "zh" else ["Give links", "Show specs", "Series page"],
        "lock_ai_override": False,
        "product_subintent": "comparison",
        "behavior_mode": _product_behavior_mode("comparison"),
        "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
    }


def _detect_mount(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> str | None:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    short_mount = SHORT_MOUNT_TOKENS.get(lowered)
    if short_mount:
        return short_mount
    for key, info in MOUNT_LIBRARY.items():
        if any(token in lowered for token in info["tokens"]):
            return key
    if session_state and session_state.get("last_mount_hint"):
        return str(session_state["last_mount_hint"])
    if short_mount:
        return short_mount
    for key, info in MOUNT_LIBRARY.items():
        if any(token in blob for token in info["tokens"]):
            return key
    return None


def _product_topic(text: str, profile_context: str = "", session_state: dict[str, Any] | None = None) -> bool:
    lowered = _lower(text)
    blob = _profile_blob(profile_context, session_state)
    if lowered in {"e", "z", "x", "rf"} and session_state and session_state.get("last_product_labels"):
        return True
    if _budget_query(lowered) and any(token in blob for token in ("sony", "索尼", "viltrox", "镜头", "air", "pancake", "chip", "50mm", "85mm", "56mm")):
        return True
    return _has_any(
        lowered,
        (
            "lens",
            "lenses",
            "product",
            "gear",
            "镜头",
            "产品",
            "镜头参数",
            "买啥",
            "推荐",
            "sony",
            "索尼",
            "50mm",
            "85mm",
            "56mm",
            "焦段",
            "air",
            "evo",
            "lab",
            "pro",
            "epic",
            "luna",
            "z1",
            "z2",
            "flash",
            "闪光灯",
            "电影机",
            "cinema",
            "饼干头",
            "pancake",
            "chip lens",
            "chip",
            "mount",
            "卡口",
        ),
    ) or bool(re.search(r"(?<!\d)(20|27|28|40|50|56|85)(mm)?(?!\d)", lowered))


def _matches_product(product: ViaProduct, text: str) -> bool:
    lowered = _lower(text)
    return any(alias in lowered for alias in product.aliases)


def _generic_product_matches(text: str, limit: int = 4) -> list[dict[str, str]]:
    lowered = _lower(text)
    matches: list[dict[str, str]] = []
    for item in PRODUCT_RULES:
        label = str(item.get("label") or "").strip()
        series = str(item.get("series") or "").strip()
        if not label:
            continue
        keywords = [str(k).strip().lower() for k in item.get("keywords") or [] if str(k).strip()]
        if label.lower() in lowered or any(keyword in lowered for keyword in keywords[:10]):
            matches.append({"label": label, "series": series, "official_url": _official_search(label)})
        if len(matches) >= limit:
            break
    return matches


def _filter_by_mount(products: list[ViaProduct], mount_key: str | None) -> list[ViaProduct]:
    if not mount_key:
        return products
    label = MOUNT_LIBRARY.get(mount_key, {}).get("label")
    if not label:
        return products
    filtered = [item for item in products if label in item.mounts]
    return filtered or products


def _filter_by_budget(products: list[ViaProduct], budget_cap: int | None) -> list[ViaProduct]:
    if not budget_cap:
        return products
    filtered = [item for item in products if item.est_price_usd <= budget_cap + 25]
    return filtered or products


def _recommended_products(user_text: str, *, profile_context: str = "", session_state: dict[str, Any] | None = None) -> list[ViaProduct]:
    lowered = _lower(user_text)
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    explicit = _filter_by_mount([item for item in CATALOG if _matches_product(item, lowered)], mount_key)
    explicit = _filter_by_budget(explicit, budget_cap)
    if explicit:
        return explicit[:3]

    family = _family_key(user_text, profile_context, session_state)
    if family == "pancake":
        return _filter_by_mount(_filter_by_budget([CATALOG[0], CATALOG[3], CATALOG[2], CATALOG[1]], budget_cap), mount_key)[:3]
    if family == "air":
        return _filter_by_mount(_filter_by_budget([CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[5], CATALOG[6]], budget_cap), mount_key)[:3]
    if family == "evo":
        return _filter_by_mount(_filter_by_budget([CATALOG[7], CATALOG[8], CATALOG[9]], budget_cap), mount_key)[:3]
    if family == "pro":
        return _filter_by_mount(_filter_by_budget([CATALOG[11], CATALOG[12], CATALOG[14], CATALOG[10]], budget_cap), mount_key)[:3]
    if family == "lab":
        return _filter_by_mount(_filter_by_budget([CATALOG[15], CATALOG[16]], budget_cap), mount_key)[:3]
    if family == "epic":
        return [CATALOG[17]]
    if family == "luna":
        return [CATALOG[18], CATALOG[19]][:3]
    if family == "lighting":
        return [CATALOG[20], CATALOG[21]][:3]

    if _apsc_query(lowered) or mount_key == "fuji_x":
        return _filter_by_mount(_filter_by_budget([CATALOG[6], CATALOG[10], CATALOG[5]], budget_cap), mount_key)[:3]

    if "85" in lowered:
        ordered = [CATALOG[9], CATALOG[13], CATALOG[14], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if "50" in lowered:
        ordered = [CATALOG[4], CATALOG[12], CATALOG[3]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if "35" in lowered:
        ordered = [CATALOG[7], CATALOG[11], CATALOG[6], CATALOG[15]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if budget_cap and budget_cap <= 320:
        ordered = [CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[0], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if mount_key == "sony_e":
        ordered = [CATALOG[4], CATALOG[7], CATALOG[8], CATALOG[11], CATALOG[9]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    if _budget_query(lowered) or _budget_query(_profile_blob(profile_context, session_state)):
        ordered = [CATALOG[4], CATALOG[3], CATALOG[2], CATALOG[1], CATALOG[5]]
        return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]

    ordered = [CATALOG[4], CATALOG[7], CATALOG[12], CATALOG[15], CATALOG[18], CATALOG[20]]
    return _filter_by_mount(_filter_by_budget(ordered, budget_cap), mount_key)[:3]


def build_product_context(
    user_text: str,
    limit: int = 5,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> list[str]:
    if not _product_topic(user_text, profile_context, session_state):
        return []
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    family = _family_key(user_text, profile_context, session_state)
    matched_products = _recommended_products(user_text, profile_context=profile_context, session_state=session_state)[:limit]
    market_rows = _bh_market_rows(matched_products)
    lines: list[str] = []
    for item in matched_products:
        lines.append(
            f"{item.label} | {item.series} | {item.format_tag} | mounts: {item.mount_label} | "
            f"best for: {item.use_case} | est_price_usd: {item.est_price_usd} | budget_tier: {item.budget_tier} | "
            f"requested_mount: {MOUNT_LIBRARY.get(mount_key, {}).get('label', '')} | budget_cap: {budget_cap or ''} | url: {item.official_url}"
        )
        market_row = market_rows.get(item.label)
        if market_row:
            lines.append(
                f"market signal | {item.label} | B&H price: {market_row.get('price') or 0} | rating: {market_row.get('rating') or 0} | "
                f"reviews: {market_row.get('review_count') or 0} | in_stock: {bool(market_row.get('in_stock'))} | url: {market_row.get('url') or ''}"
            )
    if family in {"epic", "luna"}:
        for item in _series_rule_matches(family, user_text, limit=max(1, limit - len(lines))):
            lines.append(
                f"{item['label']} | {item['series']} | series_url: {SERIES_OFFICIAL_URLS.get(item['series'], STORE_URL)} | url: {item['official_url']}"
            )
            if len(lines) >= limit:
                break
    if len(lines) < limit:
        for item in _generic_product_matches(user_text, limit - len(lines)):
            lines.append(f"{item['label']} | {item['series']} | store: {STORE_URL} | url: {item['official_url']}")
    return lines[:limit]


def _mount_reply(products: list[ViaProduct], mount_key: str | None, lang: str) -> dict[str, Any]:
    first = products[0]
    if lang == "zh":
        mount_line = "、".join(first.mounts)
        requested = MOUNT_LIBRARY.get(mount_key or "", {}).get("label_zh") or "这条路线"
        text = (
            f"如果你是在问刚才这支 {first.label}，它现在主看 {mount_line}。"
            f" 你刚刚偏向的是 {requested}，所以我会优先继续按这条卡口帮你挑。官网入口：{first.official_url}"
        )
        return {"title": "卡口说明", "text": text, "quick_actions": ["给我链接", "再便宜一点", "换 40mm"], "lock_ai_override": True}
    mount_line = ", ".join(first.mounts)
    requested = MOUNT_LIBRARY.get(mount_key or "", {}).get("label") or "that mount lane"
    text = (
        f"If you mean {first.label}, it currently sits on {mount_line}. "
        f"You are leaning toward {requested}, so I will keep filtering for that mount. Official link: {first.official_url}"
    )
    return {"title": "Mount match", "text": text, "quick_actions": ["Give link", "Lower budget", "Show 40mm"], "lock_ai_override": True}


def _spec_reply(products: list[ViaProduct], lang: str) -> dict[str, Any]:
    first = products[0]
    second = products[1] if len(products) > 1 else None
    if lang == "zh":
        specs = [f"{first.label}：约 ${first.est_price_usd}｜{first.format_tag}｜{first.mount_label}｜适合 {first.use_case_zh}"]
        if second:
            specs.append(f"{second.label}：约 ${second.est_price_usd}｜{second.format_tag}｜{second.mount_label}｜适合 {second.use_case_zh}")
        return {
            "title": "唯卓仕参数",
            "text": f"我先按唯卓仕自家产品给你讲具体一点：{'；'.join(specs)}。正式商城入口在 {STORE_URL}",
            "quick_actions": ["给我链接", "按预算选", "按卡口选"],
            "lock_ai_override": True,
        }
    specs = [f"{first.label}: about ${first.est_price_usd}, {first.format_tag}, {first.mount_label}, best for {first.use_case}"]
    if second:
        specs.append(f"{second.label}: about ${second.est_price_usd}, {second.format_tag}, {second.mount_label}, best for {second.use_case}")
    return {
        "title": "Viltrox specs",
        "text": f"I will keep this inside Viltrox. Start with {'; '.join(specs)}. Store: {STORE_URL}",
        "quick_actions": ["Give links", "Budget picks", "Mount picks"],
        "lock_ai_override": True,
    }


def _link_reply(products: list[ViaProduct], lang: str) -> dict[str, Any]:
    market_rows = _bh_market_rows(products[:2])
    listing = " / ".join(f"{item.label}：{item.official_url}" for item in products[:2]) if lang == "zh" else " / ".join(f"{item.label}: {item.official_url}" for item in products[:2])
    market_hint = ""
    lead_market = market_rows.get(products[0].label) if products else None
    if lead_market:
        market_hint = " " + _market_line(products[0], lead_market, lang)
    return {
        "title": "官方链接" if lang == "zh" else "Official links",
        "text": (
            f"我先只给你推唯卓仕自家产品。你可以先看：{listing}。总店入口：{STORE_URL}。{market_hint}".strip()
            if lang == "zh"
            else f"I will keep it Viltrox-only. Start here: {listing}. Store: {STORE_URL}.{market_hint}"
        ),
        "quick_actions": ["讲参数", "按预算选", "按机身选"] if lang == "zh" else ["Show specs", "Budget pick", "Mount pick"],
        "lock_ai_override": True,
    }


def _recommendation_reply(products: list[ViaProduct], lang: str, budget_cap: int | None) -> dict[str, Any]:
    lead = products[0]
    extra = products[1] if len(products) > 1 else None
    market_rows = _bh_market_rows(products[:2])
    scenario = _scenario_label(" ".join([lead.use_case, extra.use_case if extra else ""]), lang)
    if lang == "zh":
        budget_line = f"如果你想把预算压在 ${budget_cap} 左右，" if budget_cap else ""
        text = (
            f"{budget_line}我会先推唯卓仕自己的 {lead.label}。"
            f" 它更适合 {lead.use_case_zh}，大致在 ${lead.est_price_usd} 这一档，而且 {lead.hero_reason_zh}。"
        )
        if extra:
            text += f" 如果你想要另一个同样更稳的方向，我会再让你看 {extra.label}，它更像 {extra.use_case_zh} 这条线。"
        text += f" 如果你现在主要是 {scenario}，我会先从这条路线下手。"
        text += " " + _market_line(lead, market_rows.get(lead.label), lang)
        return {"title": "唯卓仕推荐", "text": text, "quick_actions": ["给我参数", "给我链接", "按卡口选"], "lock_ai_override": False}
    budget_line = f"If you want to stay around ${budget_cap}, " if budget_cap else ""
    text = (
        f"{budget_line}I would keep it inside Viltrox and start with {lead.label}. "
        f"It fits {lead.use_case}, sits roughly around ${lead.est_price_usd}, and {lead.hero_reason}."
    )
    if extra:
        text += f" My alternate lane would be {extra.label} for {extra.use_case}."
    text += f" If your real use case is {scenario}, this is where I would start. "
    text += _market_line(lead, market_rows.get(lead.label), lang)
    return {"title": "Viltrox picks", "text": text, "quick_actions": ["Show specs", "Give links", "Mount match"], "lock_ai_override": False}


def _comparison_reply(products: list[ViaProduct], lang: str, budget_cap: int | None, user_text: str) -> dict[str, Any]:
    lead = products[0]
    alt = products[1] if len(products) > 1 else products[0]
    market_rows = _bh_market_rows([lead, alt])
    if lang == "zh":
        budget_line = f"你现在如果想压在 ${budget_cap} 左右，" if budget_cap else ""
        text = (
            f"{budget_line}我会这样分：{_compare_axis(lead, alt, lang)} "
            f"{lead.label} 大约在 ${lead.est_price_usd} 这一档，{alt.label} 大约在 ${alt.est_price_usd} 这一档。 "
            f"如果你更看重 {_scenario_label(user_text, lang)}，我会先开 {lead.label} 的官网页给你。 "
            f"{_market_line(lead, market_rows.get(lead.label), lang)}"
        )
        if alt.label != lead.label:
            text += f" 备选方向是 {alt.label}，入口在 {alt.official_url}。"
        return {"title": "怎么选", "text": text, "quick_actions": ["给我参数", "给我链接", "按预算重排"], "lock_ai_override": False}
    budget_line = f"If you need to stay around ${budget_cap}, " if budget_cap else ""
    text = (
        f"{budget_line}here is the cleaner split: {_compare_axis(lead, alt, lang)} "
        f"{lead.label} lands around ${lead.est_price_usd}, while {alt.label} lands around ${alt.est_price_usd}. "
        f"For {_scenario_label(user_text, lang)}, I would open {lead.label} first. "
        f"{_market_line(lead, market_rows.get(lead.label), lang)}"
    )
    if alt.label != lead.label:
        text += f" Alternate route: {alt.label} at {alt.official_url}."
    return {"title": "How I would choose", "text": text, "quick_actions": ["Show specs", "Give links", "Re-rank by budget"], "lock_ai_override": False}


def _state_patch(products: list[ViaProduct], mount_key: str | None, budget_cap: int | None) -> dict[str, Any]:
    return {
        "last_product_labels": [item.label for item in products[:3]],
        "last_product_series": products[0].series if products else "",
        "last_product_summary": products[0].label if products else "",
        "last_mount_hint": mount_key or "",
        "last_budget_hint": budget_cap or 0,
    }


def _product_behavior_mode(subintent: str) -> str:
    if subintent in {"budget", "recommendation", "family_guide", "comparison"}:
        return "photography"
    if subintent in {"specs", "links", "mount"}:
        return "gear"
    return "pet"


def _classify_product_subintent(
    *,
    lowered: str,
    user_text: str,
    family: str | None,
    mount_only: bool,
    has_products: bool,
) -> str:
    if family and _family_guide_query(user_text, family):
        return "family_guide"
    if mount_only:
        return "mount"
    if _comparison_query(user_text):
        return "comparison"
    if _spec_query(user_text):
        return "specs"
    if _link_query(user_text):
        return "links"
    if has_products:
        budget_cap = _extract_budget(user_text)
        if budget_cap:
            return "budget"
        return "recommendation"
    return "catalog"


def get_via_product_reply(
    user_text: str,
    *,
    profile_context: str = "",
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    competitor_reply = handle_external_competitor_query(user_text)
    if competitor_reply:
        return competitor_reply
    if not _product_topic(user_text, profile_context, session_state):
        return None
    lang = _detect_reply_language(user_text, profile_context, session_state)
    family = _family_key(user_text, profile_context, session_state)
    mount_key = _detect_mount(user_text, profile_context, session_state)
    budget_cap = _extract_budget(user_text, profile_context, session_state)
    lowered = _lower(user_text)
    mount_only = lowered in {"e", "z", "x", "rf"} or _has_any(lowered, ("卡口", "mount"))
    subintent = _classify_product_subintent(
        lowered=lowered,
        user_text=user_text,
        family=family,
        mount_only=mount_only,
        has_products=True,
    )
    if family in {"epic", "luna"} and _series_rule_matches(family, user_text, limit=2):
        if subintent == "family_guide":
            return _series_rule_guide_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "links":
            return _series_rule_links_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "specs":
            return _series_rule_specs_reply(family, lang, user_text, mount_key, budget_cap)
        if subintent == "comparison":
            return _series_rule_comparison_reply(family, lang, user_text, mount_key, budget_cap)
    if subintent == "family_guide" and family and family in FAMILY_GUIDES:
        guide = FAMILY_GUIDES[family]
        return {
            "title": guide["zh_title"] if lang == "zh" else guide["en_title"],
            "text": guide["zh_text"] if lang == "zh" else guide["en_text"],
            "quick_actions": guide["quick_actions_zh"] if lang == "zh" else guide["quick_actions_en"],
            "lock_ai_override": False,
            "product_subintent": "family_guide",
            "behavior_mode": _product_behavior_mode("family_guide"),
            "session_state_patch": {"last_family_key": family, "last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
        }

    products = _recommended_products(user_text, profile_context=profile_context, session_state=session_state)
    if not products:
        generic = _generic_product_matches(user_text, 2)
        if not generic:
            return None
        listing = " / ".join(f"{item['label']}：{item['official_url']}" for item in generic) if lang == "zh" else " / ".join(f"{item['label']}: {item['official_url']}" for item in generic)
        return {
            "title": "唯卓仕产品" if lang == "zh" else "Viltrox products",
            "text": (f"我会优先推荐唯卓仕自家产品。你可以先从这几条官方入口开始看：{listing}。总店入口也在 {STORE_URL}" if lang == "zh" else f"I will keep recommendations inside Viltrox. Start here: {listing}. Store: {STORE_URL}"),
            "quick_actions": ["讲参数", "按预算选", "按机身选"] if lang == "zh" else ["Show specs", "Budget pick", "Mount pick"],
            "lock_ai_override": True,
            "product_subintent": "catalog",
            "behavior_mode": _product_behavior_mode("catalog"),
            "session_state_patch": {"last_mount_hint": mount_key or "", "last_budget_hint": budget_cap or 0},
        }

    subintent = _classify_product_subintent(
        lowered=lowered,
        user_text=user_text,
        family=family,
        mount_only=mount_only,
        has_products=bool(products),
    )

    if subintent == "mount":
        reply = _mount_reply(products, mount_key, lang)
    elif subintent == "comparison":
        reply = _comparison_reply(products, lang, budget_cap, user_text)
    elif subintent == "specs":
        reply = _spec_reply(products, lang)
    elif subintent == "links":
        reply = _link_reply(products, lang)
    else:
        reply = _recommendation_reply(products, lang, budget_cap)
    reply["product_subintent"] = subintent
    reply["behavior_mode"] = _product_behavior_mode(subintent)
    reply["session_state_patch"] = _state_patch(products, mount_key, budget_cap)
    return reply
