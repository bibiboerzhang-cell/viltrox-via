"""从视频深析结果里抽「创作者当前装备」(机身 + 镜头)。

背景:视频分析(Gemini final_v1)会在散文里提到创作者用的相机/镜头(如 "Sony A7IV"、
"TTArtisan Tilt 50mm"),但没抽成结构化字段,导致前端「当前设备 & 升级机会」块恒显"待接入"。
本模块用**型号词表(gazetteer)**在已有分析散文里正则匹配机身/镜头,零 LLM、零外调、纯读。
对既有分析立即生效(不必重跑)。红线:纯读文本,绝不触 viltrox_fit_score。
"""
from __future__ import annotations

import re
from typing import Any

# 机身型号词表(主流全画幅/APS-C 视频机 + 电影机),大小写不敏感、允许空格/连字符变体。
_CAMERA_BODIES = [
    # Sony
    r"Sony\s*(?:A|α|ILCE-)?7\s?(?:S\s?III|S3|R\s?V|R5|R\s?IV|R4|IV|3|II|C\s?R|CR|C)?",
    r"Sony\s*(?:A|α)?6\s?(?:700|600|400|100)", r"Sony\s*FX\s?(?:3|30|6|9|2)", r"Sony\s*ZV-?E?1?0?",
    # Canon
    r"Canon\s*(?:EOS\s*)?R\s?(?:5\s?C|5|6\s?Mark\s?II|6II|6|7|8|10|50|100|3)",
    # Nikon
    r"Nikon\s*Z\s?(?:9|8|7\s?II|7|6\s?III|6\s?II|6|5|f|fc|50|30)",
    # Fujifilm
    r"Fuji(?:film)?\s*X-?(?:T5|T4|H2S|H2|S20|S10|Pro3|E4|100V|100VI|T50)",
    # Panasonic / BMD / others
    r"Panasonic\s*(?:Lumix\s*)?(?:S5\s?II|S5|S1H|S1|GH7|GH6|GH5|G9)",
    r"(?:Blackmagic|BMPCC|BMD)\s*(?:Pocket\s*)?(?:6K|4K)?", r"DJI\s*(?:Pocket\s*3|Osmo)",
    r"RED\s*(?:Komodo|Raptor|V-Raptor)", r"Leica\s*(?:SL2|SL3|Q3|Q2|M11)",
]
# 镜头品牌(判断"用谁家镜头",给升级机会 —— 用竞品镜头=可推 Viltrox)。
_LENS_BRANDS = [
    "Viltrox", "TTArtisan", "Sigma", "Tamron", "Sony GM", "Sony G", "Canon RF", "Nikon Z",
    "Sirui", "7Artisans", "Samyang", "Rokinon", "Laowa", "Meike", "Zeiss", "Leica",
    "Fujinon", "Panasonic Lumix", "DZOFilm", "NiSi",
]
_CAMERA_RE = re.compile("|".join(f"(?:{p})" for p in _CAMERA_BODIES), re.IGNORECASE)
_LENS_RE = re.compile("|".join(re.escape(b) for b in _LENS_BRANDS), re.IGNORECASE)


def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _prose_from_analysis(result: dict[str, Any]) -> str:
    """把分析结果里可能提到设备的散文字段拼成一坨(content_summary/分镜/产品/竞品出现)。"""
    if not isinstance(result, dict):
        return ""
    payload = result.get("video_analysis_final_v1")
    if not isinstance(payload, dict):
        payload = result
    l1 = payload.get("layer1_visual_content") if isinstance(payload.get("layer1_visual_content"), dict) else {}
    parts: list[str] = [_text(l1.get("content_summary"))]
    for st in (l1.get("scene_timeline") or []):
        if isinstance(st, dict):
            parts.append(_text(st.get("what")) + " " + _text(st.get("why_it_matters")))
    pp = l1.get("product_presence")
    if isinstance(pp, dict):
        prods = pp.get("products")
        if isinstance(prods, list):
            parts.append(" ".join(_text(x) for x in prods))
        parts.append(_text(pp.get("notes")))
    cp = l1.get("competitor_presence")
    if isinstance(cp, str):
        parts.append(cp)
    elif isinstance(cp, list):
        for c in cp:
            if isinstance(c, dict):
                parts.append(_text(c.get("brand")) + " " + _text(c.get("scene")))
            elif isinstance(c, str):
                parts.append(c)
    # 归因层也常点名镜头
    l4 = payload.get("layer4_attribution") if isinstance(payload.get("layer4_attribution"), dict) else {}
    parts.append(_text(l4.get("attribution_risk")))
    return " \n".join(p for p in parts if p)


def _norm_body(m: str) -> str:
    """规范化机身型号:压空格、统一大小写显示。"""
    s = re.sub(r"\s+", " ", m.strip())
    # 品牌名首字母大写、型号大写
    return s[:40]


def extract_creator_gear(analysis_result: dict[str, Any]) -> dict[str, Any]:
    """从单条分析结果抽机身 + 镜头品牌。返回 {camera_body, camera_bodies, lens_brands}。
    抽不到返回空 dict 的对应键为空。纯读、零成本。"""
    prose = _prose_from_analysis(analysis_result)
    if not prose:
        return {"camera_body": "", "camera_bodies": [], "lens_brands": []}
    bodies: list[str] = []
    seen_b: set[str] = set()
    for m in _CAMERA_RE.findall(prose):
        b = _norm_body(m)
        k = b.lower().replace(" ", "")
        if b and k not in seen_b:
            seen_b.add(k)
            bodies.append(b)
    lenses: list[str] = []
    seen_l: set[str] = set()
    for m in _LENS_RE.findall(prose):
        b = m.strip()
        k = b.lower()
        if b and k not in seen_l:
            seen_l.add(k)
            lenses.append(b)
    return {"camera_body": bodies[0] if bodies else "", "camera_bodies": bodies[:5], "lens_brands": lenses[:6]}


def aggregate_creator_gear(analysis_results: list[dict[str, Any]]) -> dict[str, Any]:
    """跨该创作者多条分析聚合装备:机身取出现最多的,镜头品牌合并。用于 KOL 详情「当前设备」。"""
    import collections

    body_counter: collections.Counter = collections.Counter()
    lens_counter: collections.Counter = collections.Counter()
    for res in analysis_results or []:
        g = extract_creator_gear(res)
        for b in g.get("camera_bodies") or []:
            body_counter[b] += 1
        for ln in g.get("lens_brands") or []:
            lens_counter[ln] += 1
    top_body = body_counter.most_common(1)[0][0] if body_counter else ""
    return {
        "camera_body": top_body,
        "camera_bodies": [b for b, _ in body_counter.most_common(5)],
        "lens_brands": [ln for ln, _ in lens_counter.most_common(6)],
        "uses_viltrox": any("viltrox" in ln.lower() for ln in lens_counter),
    }


def gear_from_text(text: str) -> dict[str, Any]:
    """从任意文本(bio/简介/raw)抽机身+镜头品牌。兜底给没做视频深析的 KOL —— 很多创作者
    在简介里写机身(shot on Sony A7IV)。纯读、零成本。红线不触 viltrox_fit_score。"""
    text = _text(text)
    if not text:
        return {"camera_body": "", "camera_bodies": [], "lens_brands": []}
    bodies: list[str] = []
    seen_b: set[str] = set()
    for m in _CAMERA_RE.findall(text):
        b = _norm_body(m)
        k = b.lower().replace(" ", "")
        if b and k not in seen_b:
            seen_b.add(k)
            bodies.append(b)
    lenses: list[str] = []
    seen_l: set[str] = set()
    for m in _LENS_RE.findall(text):
        b = m.strip()
        if b and b.lower() not in seen_l:
            seen_l.add(b.lower())
            lenses.append(b)
    return {"camera_body": bodies[0] if bodies else "", "camera_bodies": bodies[:5], "lens_brands": lenses[:6]}
