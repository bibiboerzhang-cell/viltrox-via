"""
core/constants.py — 产品目录、权重、平台映射等全局常量
"""
from __future__ import annotations
from typing import Dict, List

VILTROX_CATALOG_PROMPT = """
【VILTROX OFFICIAL EQUIPMENT CATALOG (STRICT REFERENCE)】
When analyzing the visual evidence, strictly match the equipment against this definitive official catalog. Use your internal knowledge to recall the specific visual characteristics, mount features, and form factors of these exact models:

1. Photography Lenses:
   - LAB Series: AF 35mm F1.2 LAB (E/Z), AF 135mm F1.8 LAB (E/Z)
   - Pro Series: AF 16mm F1.8 Pro (E/Z/L), AF 27mm F1.2 Pro (E/Z/X), AF 50mm F1.4 Pro (E/Z), AF 56mm F1.2 Pro (E/Z/X), AF 75mm F1.2 Pro (E/Z/X), AF 85mm F1.4 Pro (Z)
   - Air Series: AF 9mm F2.8, AF 15mm F1.7, AF 25mm F1.7, AF 33mm F1.7, AF 35mm F1.7, AF 56mm F1.7, AF 20mm F2.8, AF 40mm F2.5, AF 50mm F2.0
   - Special/Standard: AF 28mm F4.5 Chip Lens, AF 85mm F2.0 EVO, and F1.4/F1.8 standard primes.

2. Professional Cine & DJI DL Lenses:
   - Luna Cine Zoom: 30-300mm T4.0 (PL Mount), 42-420mm T5.6 (LPL Mount)
   - EPIC Anamorphic 1.33X: 18mm to 180mm T2.0 (Strictly PL Mount)
   - ZMOVE Series Cine Lenses (PL Mount) & Cine Diopters Set
   - Raze AF Full Frame Lens Set (DJI DL Mount for Ronin)
   - AF 90mm F3.5 (DJI DL Mount)

3. Lighting & Ecosystem:
   - Flashes: Vintage Z1, Vintage Z2 Mini, Vintage Z3, JY-610/680
   - COB Lights: Ninja 10, Ninja 200, Ninja 300, Ninja 400
   - Panels/Sticks: Sprite 15C/40, Retro 08X/12X, K21, S03 Pocket, H18 Mini, WP35
   - Monitors: DC-A12800, DC-X FHD 2000, DC-V1 HD, DC-550, DC-70
   - NexusFocus F1 Servo System

4. Mount Adapters & Accessories:
   - PL Cine Adapters (PL to DL/E/Z/L/X/GFX/M43/RF)
   - EF/NF AF Adapters & Speedboosters (EF-E II, EF-Z2, EF-R3, EF-FX, etc.)
   - DG Macro Extension Tubes (NEX, GFX, FU, C, Z)
   - TC-2.0X Teleconverter
"""

# ──────────────────────────────────────────────
PLATFORM_MAP = {
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "instagram.com": "Instagram",
    "xiaohongshu.com": "Xiaohongshu",
    "xhslink.com": "Xiaohongshu",
    "bilibili.com": "Bilibili",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "fb.com": "Facebook",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

VILTROX_BRAND_KEYWORDS = ["viltrox", "唯卓仕", "nexusfocus", "nexus focus f1"]

PRODUCT_RULES = [
    # ══════════════════════════════════════════
    # LAB SERIES — Only 35mm F1.2 & 135mm F1.8
    # ══════════════════════════════════════════
    {"series": "LAB", "label": "AF 35mm F1.2 LAB", "keywords": [
        "35mm f1.2 lab", "35 f1.2 lab", "af 35mm f1.2 lab", "viltrox 35 1.2 lab",
        "lab 35", "35mm lab", "lab35", "35 lab", "viltrox lab 35"]},
    {"series": "LAB", "label": "AF 135mm F1.8 LAB", "keywords": [
        "lab 135", "135mm lab", "viltrox lab 135", "135 1.8 lab",
        "af 135mm f1.8 lab", "135mm f1.8", "viltrox 135mm", "135 lab",
        "lab135", "135mm f/1.8 lab"]},

    # ══════════════════════════════════════════
    # PRO SERIES — Full Frame F1.4
    # ══════════════════════════════════════════
    {"series": "PRO", "label": "AF 35mm F1.4 Pro FF", "keywords": [
        "35mm f1.4 pro", "35 f1.4 pro", "pro 35 f1.4", "viltrox pro 35 f1.4",
        "af 35mm f1.4", "viltrox 35mm pro"]},
    {"series": "PRO", "label": "AF 50mm F1.4 Pro FF", "keywords": [
        "50mm f1.4 pro", "50 f1.4 pro", "pro 50 f1.4", "viltrox pro 50 f1.4",
        "af 50mm f1.4", "viltrox 50mm pro", "50mm pro", "pro50"]},
    {"series": "PRO", "label": "AF 85mm F1.4 Pro FF", "keywords": [
        "85mm f1.4 pro", "85 f1.4 pro", "pro 85", "viltrox pro 85",
        "af 85mm f1.4", "viltrox 85mm pro", "85mm pro", "pro85",
        "85 f1.4 pro fe"]},

    # PRO SERIES — APS-C F1.2
    {"series": "PRO", "label": "AF 27mm F1.2 Pro APS-C", "keywords": [
        "27mm f1.2", "27 f1.2", "pro 27", "viltrox 27mm f1.2",
        "af 27mm f1.2", "27mm pro", "viltrox 27"]},
    {"series": "PRO", "label": "AF 56mm F1.2 Pro APS-C", "keywords": [
        "56mm f1.2", "56 f1.2", "pro 56", "viltrox 56mm f1.2",
        "af 56mm f1.2", "56mm pro", "pro56", "56 1.2 pro"]},
    {"series": "PRO", "label": "AF 75mm F1.2 Pro APS-C", "keywords": [
        "75mm f1.2", "75 f1.2", "pro 75", "viltrox 75mm f1.2",
        "af 75mm f1.2", "75mm pro", "pro75", "75 1.2 pro"]},

    # ══════════════════════════════════════════
    # EVO SERIES — Full Frame APO
    # ══════════════════════════════════════════
    {"series": "EVO", "label": "AF 35mm F1.8 EVO APO", "keywords": [
        "35mm f1.8 evo", "35 evo", "evo 35", "viltrox 35mm evo",
        "af 35mm f1.8 evo", "35mm evo apo", "evo35", "35 f1.8 evo"]},
    {"series": "EVO", "label": "AF 55mm F1.8 EVO APO", "keywords": [
        "55mm f1.8 evo", "55 evo", "evo 55", "viltrox 55mm evo",
        "af 55mm f1.8 evo", "55mm evo apo", "evo55", "55 f1.8 evo"]},
    {"series": "EVO", "label": "AF 85mm F2.0 EVO", "keywords": [
        "85mm f2 evo", "85mm f2.0 evo", "85 evo", "evo 85",
        "viltrox 85mm evo", "af 85mm f2 evo", "85mm evo", "evo85",
        "85 f2 evo", "85/2 evo"]},

    # ══════════════════════════════════════════
    # AIR SERIES — Full Frame
    # ══════════════════════════════════════════
    {"series": "AIR", "label": "AF 14mm F4.0 Air FF", "keywords": [
        "14mm f4 air", "14mm f4.0 air", "14 air", "af 14mm f4",
        "viltrox 14mm air", "14mm air", "air 14"]},
    {"series": "AIR", "label": "AF 20mm F2.8 Air FF", "keywords": [
        "20mm f2.8 air", "20mm f2.8", "af 20mm f2.8", "20 air",
        "viltrox 20mm", "air 20", "viltrox 20mm air"]},
    {"series": "AIR", "label": "AF 40mm F2.5 Air FF", "keywords": [
        "40mm f2.5", "40mm f2.5 air", "af 40mm", "40 air",
        "viltrox 40mm", "air 40", "viltrox 40mm air", "40mm air"]},
    {"series": "AIR", "label": "AF 50mm F2.0 Air FF", "keywords": [
        "50mm f2 air", "50mm f2.0 air", "50 air", "af 50mm f2",
        "viltrox 50mm air", "50mm air", "air 50", "50/2 air"]},

    # AIR SERIES — APS-C
    {"series": "AIR", "label": "AF 9mm F2.8 Air APS-C", "keywords": [
        "9mm f2.8 air", "9mm f2.8", "af 9mm f2.8", "9 air",
        "viltrox 9mm", "air 9", "viltrox 9mm air", "9mm air"]},
    {"series": "AIR", "label": "AF 15mm F1.7 Air APS-C", "keywords": [
        "15mm f1.7 air", "15mm f1.7", "af 15mm f1.7", "15 air",
        "viltrox 15mm", "air 15", "viltrox 15mm air", "15mm air"]},
    {"series": "AIR", "label": "AF 25mm F1.7 Air APS-C", "keywords": [
        "25mm f1.7 air", "25mm f1.7", "af 25mm f1.7", "25 air",
        "viltrox 25mm", "air 25", "viltrox 25mm air", "25mm air",
        "viltrox 25"]},
    {"series": "AIR", "label": "AF 35mm F1.7 Air APS-C", "keywords": [
        "35mm f1.7 air", "35mm f1.7", "af 35mm f1.7", "35 air",
        "viltrox 35mm air", "air 35 apsc", "35/1.7 air"]},
    {"series": "AIR", "label": "AF 56mm F1.7 Air APS-C", "keywords": [
        "56mm f1.7 air", "56mm f1.7", "af 56mm f1.7", "56 air",
        "viltrox 56mm air", "air 56", "56/1.7 air", "56mm f1.7"]},

    # ══════════════════════════════════════════
    # APS-C CLASSIC / STM LINE
    # ══════════════════════════════════════════
    {"series": "APS-C", "label": "AF 13mm F1.4 APS-C", "keywords": [
        "13mm f1.4", "af 13mm", "viltrox 13mm", "13 f1.4", "viltrox 13"]},
    {"series": "APS-C", "label": "AF 23mm F1.4 APS-C", "keywords": [
        "23mm f1.4", "af 23mm", "viltrox 23mm", "23 f1.4", "viltrox 23"]},
    {"series": "APS-C", "label": "AF 33mm F1.4 APS-C", "keywords": [
        "33mm f1.4", "af 33mm", "viltrox 33mm", "33 f1.4", "viltrox 33"]},
    {"series": "APS-C", "label": "AF 56mm F1.4 APS-C", "keywords": [
        "56mm f1.4", "af 56mm f1.4", "viltrox 56mm f1.4", "56 f1.4"]},
    {"series": "APS-C", "label": "AF 85mm F1.8 XF", "keywords": [
        "85mm f1.8 xf", "af 85mm f1.8", "viltrox 85mm f1.8 xf",
        "85 f1.8 xf", "viltrox 85 1.8"]},

    # ══════════════════════════════════════════
    # ══════════════════════════════════════════
    # EPIC SERIES — 1.33X Anamorphic Cine Primes (全系列13支)
    # 18 / 21 / 25 / 29 / 35 / 40 / 50 / 65 / 75 / 100 / 135 / 150 / 180mm
    # ══════════════════════════════════════════
    {"series": "EPIC", "label": "EPIC 18mm T2.0 1.33X", "keywords": [
        "epic 18", "epic 18mm", "viltrox epic 18", "epic 18mm anamorphic",
        "18mm 1.33x", "18mm epic"]},
    {"series": "EPIC", "label": "EPIC 21mm T2.0 1.33X", "keywords": [
        "epic 21", "epic 21mm", "viltrox epic 21", "epic 21mm anamorphic",
        "21mm 1.33x", "21mm epic"]},
    {"series": "EPIC", "label": "EPIC 25mm T2.0 1.33X", "keywords": [
        "epic 25", "epic 25mm", "viltrox epic 25", "epic 25mm anamorphic",
        "25mm 1.33x", "25mm epic", "epic anamorphic 25"]},
    {"series": "EPIC", "label": "EPIC 29mm T2.0 1.33X", "keywords": [
        "epic 29", "epic 29mm", "viltrox epic 29", "epic 29mm anamorphic",
        "29mm 1.33x", "29mm epic"]},
    {"series": "EPIC", "label": "EPIC 35mm T2.0 1.33X", "keywords": [
        "epic 35", "epic 35mm", "viltrox epic 35", "epic 35mm anamorphic",
        "35mm 1.33x", "35mm epic", "epic anamorphic 35"]},
    {"series": "EPIC", "label": "EPIC 40mm T2.0 1.33X", "keywords": [
        "epic 40", "epic 40mm", "viltrox epic 40", "epic 40mm anamorphic",
        "40mm 1.33x", "40mm epic"]},
    {"series": "EPIC", "label": "EPIC 50mm T2.0 1.33X", "keywords": [
        "epic 50", "epic 50mm", "viltrox epic 50", "epic 50mm anamorphic",
        "50mm 1.33x", "50mm epic", "epic anamorphic 50"]},
    {"series": "EPIC", "label": "EPIC 65mm T2.8 1.33X Macro", "keywords": [
        "epic 65", "epic 65mm", "viltrox epic 65", "epic 65mm anamorphic",
        "65mm 1.33x", "65mm epic", "epic 65mm macro"]},
    {"series": "EPIC", "label": "EPIC 75mm T2.0 1.33X", "keywords": [
        "epic 75", "epic 75mm", "viltrox epic 75", "epic 75mm anamorphic",
        "75mm 1.33x", "75mm epic", "75mm t2.1", "epic anamorphic 75"]},
    {"series": "EPIC", "label": "EPIC 100mm T2.0 1.33X", "keywords": [
        "epic 100", "epic 100mm", "viltrox epic 100", "epic 100mm anamorphic",
        "100mm 1.33x", "100mm epic", "epic anamorphic 100"]},
    {"series": "EPIC", "label": "EPIC 135mm T2.4 1.33X", "keywords": [
        "epic 135", "epic 135mm", "viltrox epic 135", "epic 135mm anamorphic",
        "135mm 1.33x", "135mm epic", "epic anamorphic 135", "epic 135mm t2.4"]},
    {"series": "EPIC", "label": "EPIC 150mm T2.0 1.33X", "keywords": [
        "epic 150", "epic 150mm", "viltrox epic 150", "epic 150mm anamorphic",
        "150mm 1.33x", "150mm epic"]},
    {"series": "EPIC", "label": "EPIC 180mm T2.0 1.33X", "keywords": [
        "epic 180", "epic 180mm", "viltrox epic 180", "epic 180mm anamorphic",
        "180mm 1.33x", "180mm epic"]},
    # EPIC general / set terms
    {"series": "EPIC", "label": "EPIC Cinema Series", "keywords": [
        "epic series", "viltrox epic", "viltrox anamorphic", "1.33x anamorphic",
        "1.33x squeeze", "epic cine", "epic maestro", "epic memento",
        "oval bokeh", "blue streak flare", "silver flare", "anamorphic mumps",
        "pl mount cine", "epic lens set", "epic full set"]},
    # ══════════════════════════════════════════
    # LUNA SERIES — Professional Cinema Zoom
    # ══════════════════════════════════════════
    {"series": "LUNA", "label": "LUNA 30-300mm T4.0", "keywords": [
        "luna 30-300", "luna 30300", "30-300mm t4", "viltrox luna 30",
        "30-300 t4", "luna zoom", "viltrox luna", "30-300mm luna",
        "luna 30 300", "10x zoom 30-300"]},
    {"series": "LUNA", "label": "LUNA 42-420mm T5.6", "keywords": [
        "luna 42-420", "luna 42420", "42-420mm t5.6", "viltrox luna 42",
        "42-420 t5.6", "42-420mm luna", "luna 42 420", "10x zoom 42-420",
        "lpl mount zoom", "viltrox large format zoom"]},
    # ══════════════════════════════════════════
    # DL SERIES — DJI Inspire 3 / Ronin 4D Native
    # ══════════════════════════════════════════
    {"series": "DL", "label": "AF 90mm F3.5 DL", "keywords": [
        "90mm f3.5 dl", "af 90mm f3.5", "viltrox 90mm dl", "90mm dl mount",
        "90 f3.5 dl", "viltrox dl 90", "dl 90", "90mm dji dl",
        "viltrox 90mm f3.5", "af 90 f3.5", "90mm inspire", "90mm ronin 4d"]},
    {"series": "DL", "label": "Raze AF Lens Set DL", "keywords": [
        "raze af", "raze lens set", "raze full frame", "viltrox raze",
        "raze dl", "raze ronin", "raze inspire", "raze cine set",
        "viltrox raze af", "dl mount lens set"]},
    # ══════════════════════════════════════════
    # LENS ADAPTERS — EF Mount Series
    # ══════════════════════════════════════════
    {"series": "ADAPTER", "label": "EF-FX1/FX2 (Canon EF -> Fuji X)", "keywords": [
        "ef-fx1", "ef-fx2", "ef-fx1 pro", "ef fx1", "ef fx2",
        "canon ef to fuji", "ef to x-mount adapter", "ef-fx"]},
    {"series": "ADAPTER", "label": "EF-GFX (Canon EF -> Fuji GFX)", "keywords": [
        "ef-gfx", "ef gfx", "ef-gfx pro", "canon ef to gfx",
        "ef to medium format", "fuji gfx adapter"]},
    {"series": "ADAPTER", "label": "EF-M1/M2/M43 (Canon EF -> MFT)", "keywords": [
        "ef-m1", "ef-m2", "ef-m43", "ef m1", "ef m2", "ef m43",
        "canon ef to m43", "ef to micro four thirds", "ef-m2 booster",
        "speed booster viltrox", "0.71x booster"]},
    {"series": "ADAPTER", "label": "EF-Z/Z2 (Canon EF -> Nikon Z)", "keywords": [
        "ef-z", "ef-z2", "ef z", "ef z2", "canon ef to nikon z",
        "ef to z mount", "viltrox ef-z"]},
    {"series": "ADAPTER", "label": "EF-L/L Pro (Canon EF -> L Mount)", "keywords": [
        "ef-l", "ef-l pro", "ef l pro", "canon ef to l mount",
        "ef to leica l", "ef l adapter", "viltrox ef-l"]},
    {"series": "ADAPTER", "label": "EF-E/NEX (Canon EF -> Sony E)", "keywords": [
        "ef-e ii", "ef-e5", "ef-nex", "ef-sony", "ef e ii", "ef e5",
        "canon ef to sony e", "ef to e-mount", "viltrox ef-e",
        "ef-nex adapter", "ef to nex"]},
    {"series": "ADAPTER", "label": "EF-Nikon (Canon EF -> Nikon F)", "keywords": [
        "ef-nikon", "ef nikon", "canon ef to nikon f",
        "ef to f-mount", "viltrox ef nikon"]},

    # NF Mount Adapters (Nikon F -> other mounts)
    {"series": "ADAPTER", "label": "NF-M43/M1 (Nikon F -> MFT)", "keywords": [
        "nf-m43", "nf-m1", "nf m43", "nf m1",
        "nikon f to m43", "nikon to micro four thirds"]},
    {"series": "ADAPTER", "label": "NF-Z (Nikon F -> Nikon Z)", "keywords": [
        "nf-z", "nf z", "nikon f to nikon z", "viltrox nf-z"]},
    {"series": "ADAPTER", "label": "NF-E/Sony (Nikon F -> Sony E)", "keywords": [
        "nf-e1", "nf-sony", "nf e1", "nf sony",
        "nikon f to sony e", "nikon to e-mount"]},

    # E-Z Adapter (Sony E -> Nikon Z)
    {"series": "ADAPTER", "label": "E-Z (Sony E -> Nikon Z)", "keywords": [
        "e-z adapter", "e-z v5", "sony e to nikon z",
        "viltrox e-z", "e to z adapter", "e-z v5.0"]},

    # ══════════════════════════════════════════
    # ACCESSORIES
    # ══════════════════════════════════════════
    {"series": "ACCESSORY", "label": "TC-2.0X Teleconverter", "keywords": [
        "tc-2.0x", "tc 2.0x", "viltrox teleconverter", "2x teleconverter",
        "viltrox tc", "tc2.0x", "teleconverter"]},
    {"series": "ACCESSORY", "label": "Battery Charging Case", "keywords": [
        "battery case", "charging case", "dual bay", "battery charger viltrox",
        "viltrox battery case", "dual bay charging", "viltrox charger"]},
    {"series": "ACCESSORY", "label": "X100 Conversion Lens", "keywords": [
        "x100 conversion", "x100 lens", "viltrox x100", "conversion lens fuji x100",
        "x100vi lens", "x100 wide", "x100 tele"]},

    # ══════════════════════════════════════════
    # NEXUSFOCUS
    # ══════════════════════════════════════════
    {"series": "NEXUSFOCUS", "label": "NexusFocus F1 PL-E", "keywords": [
        "nexusfocus", "nexusfocus f1", "nexus focus f1", "nexusfocus f 1",
        "nexus focus", "nexusfocus adapter", "pl-e adapter", "pl e adapter",
        "nexusfocus pl", "viltrox nexus", "fiz motor", "fiz control",
        "pl mount autofocus", "pl-e autofocus", "manual lens autofocus adapter",
        "nexusfocus app"]}
]

CAMERA_KEYWORDS = [
    "sony", "a7s3", "a7s iii", "a7iv", "a7 iv", "fx3", "fx30", "fx6", "fx9",
    "canon", "r5", "r6", "r7", "r8", "c70", "c80",
    "nikon", "z6", "z6iii", "z8", "z9", "zf",
    "fujifilm", "fuji", "x-t5", "xt5", "x-s20", "xs20", "gfx", "x100",
    "leica", "lumix", "s5ii", "s9", "bmpcc", "blackmagic", "arri", "red", "komodo",
    "hasselblad", "phase one",
]

LENS_KEYWORDS = [
    "13mm", "16mm", "20mm", "24mm", "25mm", "27mm", "28mm", "33mm", "35mm", "40mm",
    "50mm", "56mm", "75mm", "85mm", "100mm", "135mm",
    "f1.2", "f1.4", "f1.7", "f1.8", "f2.0", "f2.8",
    "anamorphic", "xf", "e mount", "x mount", "rf", "z mount", "pl", "l mount",
]

SPAM_COMMENT_KEYWORDS = [
    "dm me", "check my page", "promote it", "promotion", "promosm",
    "telegram", "whatsapp", "wa.me", "cash app", "crypto", "forex",
    "binary", "investment", "claim prize", "giveaway", "inbox me",
]


# ──────────────────────────────────────────────
# Dynamic quality scoring weights by content genre
# Tech dims:       exposure focus stability color_grade composition lighting editing
# Marketing dims:  storytelling hook viltrox_branding
# Weights must sum to 100
# ──────────────────────────────────────────────
GENRE_QUALITY_WEIGHTS: dict[str, dict[str, int]] = {
    "review": {
        "exposure": 8, "focus": 10, "stability": 8, "color_grade": 7,
        "composition": 7, "lighting": 7, "editing": 8,          # tech = 55
        "storytelling": 15, "hook": 15, "viltrox_branding": 15, # marketing = 45
    },
    "comparison": {
        "exposure": 8, "focus": 10, "stability": 8, "color_grade": 7,
        "composition": 7, "lighting": 6, "editing": 8,           # tech = 54
        "storytelling": 12, "hook": 12, "viltrox_branding": 22,  # marketing = 46
    },
    "tutorial": {
        "exposure": 7, "focus": 10, "stability": 9, "color_grade": 5,
        "composition": 5, "lighting": 6, "editing": 8,           # tech = 50
        "storytelling": 18, "hook": 17, "viltrox_branding": 15,  # marketing = 50
    },
    "cinematic": {
        "exposure": 14, "focus": 14, "stability": 12, "color_grade": 18,
        "composition": 14, "lighting": 14, "editing": 10,        # tech = 96
        "storytelling": 2, "hook": 2, "viltrox_branding": 0,    # marketing = 4 (pure craft)
    },
    "vlog": {
        "exposure": 8, "focus": 9, "stability": 10, "color_grade": 7,
        "composition": 7, "lighting": 7, "editing": 10,          # tech = 58
        "storytelling": 14, "hook": 14, "viltrox_branding": 14,  # marketing = 42
    },
    "unboxing": {
        "exposure": 8, "focus": 10, "stability": 8, "color_grade": 6,
        "composition": 6, "lighting": 7, "editing": 7,           # tech = 52
        "storytelling": 10, "hook": 15, "viltrox_branding": 23,  # marketing = 48
    },
    "showcase": {
        "exposure": 10, "focus": 12, "stability": 10, "color_grade": 12,
        "composition": 12, "lighting": 12, "editing": 8,         # tech = 76
        "storytelling": 5, "hook": 7, "viltrox_branding": 12,   # marketing = 24
    },
    "bts": {
        "exposure": 7, "focus": 8, "stability": 8, "color_grade": 6,
        "composition": 6, "lighting": 6, "editing": 9,           # tech = 50
        "storytelling": 16, "hook": 13, "viltrox_branding": 21,  # marketing = 50
    },
    "default": {
        "exposure": 10, "focus": 10, "stability": 10, "color_grade": 10,
        "composition": 10, "lighting": 10, "editing": 10,        # tech = 70
        "storytelling": 10, "hook": 10, "viltrox_branding": 10,  # marketing = 30
    },
}

TECH_DIMS     = ["exposure", "focus", "stability", "color_grade", "composition", "lighting", "editing"]
MARKETING_DIMS = ["storytelling", "hook", "viltrox_branding"]

# ── Three-axis framework ──
# Axis 1: Brand Exposure  — how visibly does Viltrox appear?
BRAND_EXPOSURE_DIMS = ["viltrox_branding", "logo_visibility", "product_screen_time", "close_up_quality", "thumbnail_brand"]
# Axis 2: Storytelling    — does the audience want to buy after watching?
STORYTELLING_DIMS   = ["hook", "storytelling", "audience_fit", "authenticity", "conclusion_strength"]
# Axis 3: Tech Floor      — is it watchable? (pass/warning/fail, not a score)
TECH_FLOOR_DIMS     = ["exposure", "stability", "color_grade", "composition", "lighting", "editing"]
# focus removed from floor — importance varies hugely by vertical

# ── Three-axis framework ──
# Axis 1: Brand Exposure  — how visibly does Viltrox appear?
BRAND_EXPOSURE_DIMS = ["viltrox_branding", "logo_visibility", "product_screen_time", "close_up_quality", "thumbnail_brand"]
# Axis 2: Storytelling    — does the audience want to buy after watching?
STORYTELLING_DIMS   = ["hook", "storytelling", "audience_fit", "authenticity", "conclusion_strength"]
# Axis 3: Tech Floor      — is it watchable? (pass/warning/fail, not a score)
TECH_FLOOR_DIMS     = ["exposure", "stability", "color_grade", "composition", "lighting", "editing"]
# focus removed from floor — importance varies hugely by vertical

# Tech floor thresholds
TECH_FLOOR_PASS    = 6.5   # avg >= 6.5 → pass
TECH_FLOOR_WARNING = 5.0   # avg >= 5.0 → warning
# below 5.0 → fail


VERTICAL_WEIGHTS: dict[str, dict] = {
    "wedding": {
        "label_cn": "婚礼/情感摄影",
        "tech":   {"exposure":35, "focus":30, "stability":20, "color_grade":10, "composition":5, "lighting":0, "editing":0},
        "mkt":    {"storytelling":50, "hook":20, "viltrox_branding":30},
        "key_dims": ["focus", "exposure", "storytelling"],
        "what_matters": "情感张力、人物清晰、光线自然",
    },
    "food": {
        "label_cn": "美食/产品静物",
        "tech":   {"exposure":20, "focus":20, "stability":10, "color_grade":35, "composition":15, "lighting":0, "editing":0},
        "mkt":    {"storytelling":25, "hook":35, "viltrox_branding":40},
        "key_dims": ["color_grade", "composition", "hook"],
        "what_matters": "色彩诱人、构图精准、产品可见",
    },
    "lifestyle": {
        "label_cn": "生活方式/Vlog",
        "tech":   {"exposure":15, "focus":15, "stability":30, "color_grade":20, "composition":10, "lighting":0, "editing":10},
        "mkt":    {"storytelling":40, "hook":35, "viltrox_branding":25},
        "key_dims": ["stability", "storytelling", "hook"],
        "what_matters": "真实感、节奏流畅、观众代入感",
    },
    "review": {
        "label_cn": "专业评测",
        "tech":   {"exposure":20, "focus":30, "stability":15, "color_grade":15, "composition":10, "lighting":10, "editing":0},
        "mkt":    {"storytelling":30, "hook":25, "viltrox_branding":45},
        "key_dims": ["focus", "viltrox_branding", "storytelling"],
        "what_matters": "专业可信度、产品展示清晰、结论有说服力",
    },
    "cinematic": {
        "label_cn": "电影感/艺术创作",
        "tech":   {"exposure":20, "focus":20, "stability":15, "color_grade":30, "composition":15, "lighting":0, "editing":0},
        "mkt":    {"storytelling":40, "hook":30, "viltrox_branding":30},
        "key_dims": ["color_grade", "composition", "storytelling"],
        "what_matters": "视觉震撼、色彩深度、镜头语言",
    },
    "sports": {
        "label_cn": "体育/动态/舞蹈",
        "tech":   {"exposure":15, "focus":45, "stability":25, "color_grade":5, "composition":10, "lighting":0, "editing":0},
        "mkt":    {"storytelling":20, "hook":50, "viltrox_branding":30},
        "key_dims": ["focus", "stability", "hook"],
        "what_matters": "追焦能力、动态稳定、视觉冲击开场",
    },
    "travel": {
        "label_cn": "旅行/风光",
        "tech":   {"exposure":25, "focus":15, "stability":20, "color_grade":25, "composition":15, "lighting":0, "editing":0},
        "mkt":    {"storytelling":35, "hook":30, "viltrox_branding":35},
        "key_dims": ["composition", "color_grade", "storytelling"],
        "what_matters": "构图广阔、色彩还原、叙事带入感",
    },
    "portrait": {
        "label_cn": "人像/写真",
        "tech":   {"exposure":20, "focus":35, "stability":10, "color_grade":20, "composition":15, "lighting":0, "editing":0},
        "mkt":    {"storytelling":20, "hook":30, "viltrox_branding":50},
        "key_dims": ["focus", "viltrox_branding", "color_grade"],
        "what_matters": "人物清晰、虚化效果、Viltrox镜头特性展示",
    },
    "tutorial": {
        "label_cn": "教程/教学",
        "tech":   {"exposure":15, "focus":20, "stability":25, "color_grade":10, "composition":10, "lighting":10, "editing":10},
        "mkt":    {"storytelling":45, "hook":30, "viltrox_branding":25},
        "key_dims": ["stability", "storytelling", "hook"],
        "what_matters": "步骤清晰、讲解准确、观众可复制",
    },
    "commercial": {
        "label_cn": "商业/广告",
        "tech":   {"exposure":20, "focus":20, "stability":15, "color_grade":25, "composition":15, "lighting":5, "editing":0},
        "mkt":    {"storytelling":30, "hook":30, "viltrox_branding":40},
        "key_dims": ["color_grade", "viltrox_branding", "hook"],
        "what_matters": "画面精致、品牌感强、转化意图明确",
    },
    "default": {
        "label_cn": "其他内容",
        "tech":   {"exposure":14, "focus":15, "stability":14, "color_grade":14, "composition":14, "lighting":14, "editing":15},
        "mkt":    {"storytelling":34, "hook":33, "viltrox_branding":33},
        "key_dims": ["exposure", "focus", "storytelling"],
        "what_matters": "整体质量",
    },
}

# Mapping from content_genre to vertical
GENRE_TO_VERTICAL: dict[str, str] = {
    "review":       "review",
    "comparison":   "review",
    "unboxing":     "review",
    "tutorial":     "tutorial",
    "cinematic":    "cinematic",
    "showcase":     "cinematic",
    "bts":          "lifestyle",
    "vlog":         "lifestyle",
    "portrait":     "portrait",
    "wedding":      "wedding",
    "food":         "food",
    "travel":       "travel",
    "sports":       "sports",
    "dance":        "sports",
    "commercial":   "commercial",
    "lifestyle":    "lifestyle",
}

