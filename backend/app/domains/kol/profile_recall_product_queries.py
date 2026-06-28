"""Product-line query templates, persona/SKU mappings, and query-text resolution
for KOL profile vector recall. Moved verbatim from profile_recall.py (behavior-preserving).

These templates are independent query_text inputs to recall; they never write any
scoring field (viltrox_fit_score / recall_rank_score / vector_score)."""
from __future__ import annotations

from typing import Any


PRODUCT_QUERY_TEXTS = {
    "35mm_f12_lab": """Product query profile: Viltrox AF 35mm F1.2 LAB.
Creator use cases: environmental portrait, street photography, documentary storytelling, wedding and engagement photography, low-light portrait, editorial fashion, hybrid photo and video, premium full-frame storyteller.
Desired creator profile: high quality people and scene storytelling, strong portrait or street portfolio, visible lens or camera review credibility, Viltrox or mirrorless lens experience, cinematic natural-light style, premium full-frame audience.""",
    "35mm_f17_air": """Product query profile: Viltrox AF 35mm F1.7 AIR.
Creator use cases: lightweight everyday carry, travel photography, vlog and creator kit, casual portrait, street walkaround, budget APS-C creator, compact Sony Fuji Nikon setup.
Desired creator profile: mobile everyday creator, travel or street shooter, beginner-friendly gear reviewer, light kit advocate, practical budget lens audience, casual hybrid photo and video workflow.""",
    # ── 产品线 query 模板扩展(2026-06-13,B 真 SKU 驱动)。键=产品线 query_profile,
    #    文案据 vkpi_products 真 model_name/marketing_name 写;product→人群映射在下方 PRODUCT_LINE_PERSONAS。
    #    这些模板对 recall 是独立 query_text,绝不写任何评分字段。
    "monitor_dc550pro2": """Product query profile: Viltrox DC-550 PRO II on-camera field monitor (5.5 inch 1400nit), also DC-X 6 inch 2000nit.
Creator use cases: on-set monitoring, multi-camera production, gimbal and rig operators, narrative filmmaking, commercial and ad shoots, music video, automotive and food and wedding videography, documentary, DP and camera operator monitoring workflow.
Desired creator profile: filmmaker, videographer, cinematographer, camera operator, DP, multi-cam live shooter, gear reviewer covering monitors and rigs; broad set of shooting verticals, not only literal monitor reviews.""",
    "flash_vintage_z1pro": """Product query profile: Viltrox Vintage Z1 PRO retro TTL on-camera flash (24Ws HSS), also Vintage Z2 Mini and Spark Z3 TTL flash.
Creator use cases: wedding and engagement lighting, on-location portrait, studio and home-studio portrait, off-camera flash, HSS daylight fill, retro and editorial portrait, headshot, creative lighting tutorial.
Desired creator profile: wedding photographer, portrait photographer, studio shooter, lighting educator, flash and strobe reviewer; people-lighting focused audience.""",
    "lens_evo_portrait": """Product query profile: Viltrox EVO lightweight portrait primes (35mm/55mm F1.8, 85mm F2.0 full-frame, Sony E and Nikon Z).
Creator use cases: lightweight portrait, everyday people photography, environmental portrait, hybrid photo and video, travel-friendly portrait kit, natural-light shooter.
Desired creator profile: portrait and lifestyle photographer, light-kit advocate, mirrorless full-frame creator on Sony or Nikon, hybrid shooter.""",
    "lens_lab_flagship": """Product query profile: Viltrox LAB flagship primes (35mm F1.2 and 135mm F1.8, full-frame Sony E and Nikon Z).
Creator use cases: premium portrait, editorial fashion, telephoto compression portrait, low-light cinematic, event and sports portrait, professional people storytelling.
Desired creator profile: professional portrait or editorial photographer, premium full-frame audience, strong portfolio, lens-review credibility, cinematic natural-light style.""",
    "lens_air_wide": """Product query profile: Viltrox Air ultra-light primes including 14mm and 20mm ultra-wide, plus compact 25/35/40mm (Sony E, Nikon Z, Fuji X).
Creator use cases: ultra-wide landscape and astro and cityscape, vlog and run-and-gun, travel, real-estate and interior, lightweight everyday carry, content creator kit.
Desired creator profile: travel and landscape creator, vlogger, real-estate or interior shooter, light-kit advocate, mobile hybrid creator.""",
    "lens_pro_aps": """Product query profile: Viltrox Pro fast primes (27mm/56mm/75mm F1.2 APS-C, 50mm F1.4 and 85mm F1.4 Pro, Sony E / Fuji X / Nikon Z).
Creator use cases: fast-aperture portrait, low-light people photography, street and lifestyle, wedding and event, commercial portrait, APS-C and full-frame hybrid.
Desired creator profile: portrait and lifestyle photographer, wedding or event shooter, APS-C Fuji or Sony or Nikon creator, fast-prime enthusiast, lens reviewer.""",
    "cine_epic_anamorphic": """Product query profile: Viltrox EPIC PL anamorphic and spherical cine lenses (1.33X full-frame, plus 90mm F3.5 DL for DJI).
Creator use cases: narrative and short-film, anamorphic cinematic look, music video, commercial and ad film, indie filmmaking, cine rig and gimbal work.
Desired creator profile: filmmaker, cinematographer, DP, cine-lens reviewer, anamorphic enthusiast, indie and commercial video producer.""",
}

# 产品线 query_profile → 中文产品线标签(透出/筛选用,SKU 生码不直显)+ 关键 SKU 营销名锚点。
# 仅作展示与人群说明,绝不参与评分;tag_product_lines() 与前端复用同一份语义。
PRODUCT_LINE_PERSONAS = {
    "monitor_dc550pro2": {"label": "550pro 监视器", "persona": "影视/多机位/现场监看创作者"},
    "flash_vintage_z1pro": {"label": "Z1pro 闪光", "persona": "婚礼/人像/棚拍灯光"},
    "lens_evo_portrait": {"label": "EVO 人像镜头", "persona": "轻量人像/生活方式"},
    "lens_lab_flagship": {"label": "LAB 旗舰镜头", "persona": "专业人像/编辑时尚"},
    "lens_air_wide": {"label": "Air 超广镜头", "persona": "风光/星空/旅行/Vlog"},
    "lens_pro_aps": {"label": "Pro 大光圈镜头", "persona": "人像/街拍/婚礼"},
    "cine_epic_anamorphic": {"label": "EPIC 电影镜头", "persona": "影视/广告/独立电影"},
    "35mm_f12_lab": {"label": "LAB 旗舰镜头", "persona": "环境人像/街拍/纪实"},
    "35mm_f17_air": {"label": "Air 轻量镜头", "persona": "旅行/街拍/Vlog"},
}

PRODUCT_SKU_ALIASES = {
    "35MMF12LAB": "35mm_f12_lab",
    "35MMF12": "35mm_f12_lab",
    "AF35MMF12LAB": "35mm_f12_lab",
    "AF35MMF12LABFE": "35mm_f12_lab",
    "AF35MMF12LABZ": "35mm_f12_lab",
    "35MMF17AIR": "35mm_f17_air",
    "35MMF17": "35mm_f17_air",
    "AF35MMF17AIR": "35mm_f17_air",
    "AF35MMF17AIRFE": "35mm_f17_air",
    "AF35MMF17AIRX": "35mm_f17_air",
    "AF35MMF17AIRZ": "35mm_f17_air",
    # ── 产品线锚 SKU → query_profile(规范化去非字母数字后比对;真 vkpi_products.sku 派生)。
    "VLMON016": "monitor_dc550pro2",            # DC-550 PRO II
    "DC550PROLLPORTABLE55INCHHDCAMERAMONITOR": "monitor_dc550pro2",
    "DCXFHD2000NITS6INCHCAMERAMONITOR": "monitor_dc550pro2",
    "VLLIT092": "flash_vintage_z1pro",          # Vintage Z1 PRO-N
    "VLLIT093": "flash_vintage_z1pro",
    "VLLIT094": "flash_vintage_z1pro",
    "VLLIT095": "flash_vintage_z1pro",
    "VINTAGEZ1RETROONCAMERAFLASH": "flash_vintage_z1pro",
    "AF35MMF18EVOFE": "lens_evo_portrait",
    "AF55MMF18EVOFE": "lens_evo_portrait",
    "AF85MMF20EVOFE": "lens_evo_portrait",
    "AF135MMF18LABFE": "lens_lab_flagship",
    "AF135MMF18LABZ": "lens_lab_flagship",
    "AF14MMF40AIRFE": "lens_air_wide",
    "AF20MMF28AIRFE": "lens_air_wide",
    "AF27MMF12PROFE": "lens_pro_aps",
    "AF50MMF14PROFE": "lens_pro_aps",
    "AF56MMF12PROFE": "lens_pro_aps",
    "AF75MMF12PROFE": "lens_pro_aps",
    "AF85MMF14PROZ": "lens_pro_aps",
}


def _normalise_sku(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def resolve_query_text(*, query_text: str = "", product_sku: str = "") -> tuple[str, dict[str, Any]]:
    explicit = str(query_text or "").strip()
    if explicit:
        return explicit, {"query_text_provided": True, "product_sku": str(product_sku or "").strip()}
    raw_sku = str(product_sku or "").strip()
    alias = PRODUCT_SKU_ALIASES.get(_normalise_sku(raw_sku), "")
    if not alias and raw_sku in PRODUCT_QUERY_TEXTS:
        alias = raw_sku
    if not alias:
        raise ValueError("query_text or supported product_sku is required")
    return PRODUCT_QUERY_TEXTS[alias], {"query_text_provided": False, "product_sku": raw_sku, "query_profile": alias}
