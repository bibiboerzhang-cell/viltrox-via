"""镜头出镜证据抽取(final_v1 深析缓存 → Viltrox 产品提及 → 目录归一)。

输入:vkpi_analysis_cache.result(derive_method=video_analysis_final_v1)。六层结果里
产品识别只存在于散文字段(layer1.product_presence / brand_exposure / content_summary /
scene_timeline、layer4.product_contribution、layer6.product_proof_score.evidence、
raw.content_topic 等);结构化 viltrox_products_all 在存量里全空,所以这里用
「品牌词锚定 + 型号 token 截取」的确定性抽取,零 LLM、零外调。

归一:只认 vkpi_products 目录(+ vkpi_product_aliases 别名表 + products/product_aliases_lens
口语别名表);
  * sku        —— 唯一命中一个 SKU(含卡口);
  * family     —— 命中同一镜头家族但卡口未知 / 目录无该卡口 → 多 SKU 候选;仅系列提及(Pro / Air / EVO / LAB / EPIC)同归 family(lens_key=series:xxx);
  * unresolved —— 目录无此型号 / 多家族歧义 → 保留原文,绝不杜撰。
modality(画面 / 字幕·文字 / 口播)按提及所在句子的线索词判定,判不出 = unspecified。
v_relevance 三态只读投影(v_relevance_for):
  * confirmed —— 归一到目录(sku/family)且画面 / 口播 / 字幕明确提及;
  * likely    —— 提及但未归一 / 仅系列 / 归一了但无明确 modality / 只出现在建议性字段;
  * none      —— 整条深析零提及(视频级,由扫描账本给出)。
红线:纯读;绝不写 viltrox_fit_score / 不触 rule_v0。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domains.products.product_aliases import generated_aliases_for_product, normalize_alias
from app.domains.products.product_aliases_lens import (
    ALIAS_TABLE_VERSION,
    lookup_lens_alias,
    rewrite_mount_phrases,
    series_label,
    series_only_codes,
)


EXTRACTOR_VERSION = "lens_evidence_v2"
FINAL_DERIVE_METHOD = "video_analysis_final_v1"
RESOLUTIONS = ("sku", "family", "unresolved")
MODALITIES = ("visual", "text", "voice", "unspecified")
V_RELEVANCE = ("confirmed", "likely", "none")
V_RELEVANCE_LABELS = {"confirmed": "确认出镜", "likely": "疑似出镜", "none": "无出镜"}
EXPLICIT_MODALITIES = frozenset({"visual", "voice", "text"})
# 建议性字段(裁决 / 钩子)里的点名不是出镜证据,只能算 likely。
ADVISORY_FIELDS = frozenset({"final_verdict", "key_hook"})
# 仅系列提及只认「产品出镜」类字段;裁决 / 归因里的「推 Pro 系列」是建议不是证据。
SERIES_SOURCE_FIELDS = frozenset({"product_presence", "brand_exposure", "scene_timeline", "content_summary", "raw_content_topic", "raw_viltrox_lens", "raw_viltrox_products_all"})
MAX_MENTIONS_PER_CACHE = 12
MAX_CANDIDATES = 12
SERIES_KEY_PREFIX = "series:"

_BRAND_RE = re.compile(
    r"(?:viltrox|唯卓仕|唯卓|维卓仕|维卓)\s*(?:的|品牌的|品牌|全新|新品|新款|这款|这支|那支|那款|出品的)*[\s:：,，]*",
    re.IGNORECASE,
)
# 型号 token:规格词 / 系列词 / 卡口词 / 含数字的型号码;其余词(如 is / 镜头)即截止。
_TOKEN_RE = re.compile(
    r"^(?:af|mf|fe|ef|dc|dg|tc|pl|rf|xf|z|x|l|e|s|m43|ii|iii|iv|pro|air|evo|lab|chip|epics?|vintage|cine|macro|"
    r"anamorphic|maestro|memento|nexusfocus|nexus|spark|sprite|ninja|retro|mini|plus|kit|max|ultra|"
    r"full-?frame|aps-?c|apsc|"
    r"(?:fe|e|z|xf|x|l|rf|ef|pl|m43)-?mount|"
    r"\d[\w.\-/+]*|[a-z]{1,3}-(?:[a-z]?\d[\w.\-/+]*|[a-z]{1,2})|[a-z]{1,3}\d[\w.\-/+]*|f/?\d(?:\.\d)?|t/?\d(?:\.\d)?)$",
    re.IGNORECASE,
)
_TRAILING_DROP = {"full-frame", "fullframe", "full", "frame", "aps-c", "apsc", "kit", "mount"}
_LEADING_DROP = {"ii", "iii", "iv", "chip", "mini", "plus", "max", "ultra", "kit", "full-frame", "fullframe", "aps-c", "apsc"}
# 「Viltrox Pro 75mm F1.2」:系列词在前不是丢掉,挪到尾部(系列是硬约束,丢了会错归家族)。
_LEADING_SERIES = {"pro", "air", "evo", "lab"}
_SERIES_SLASH_RE = re.compile(r"\b(pro|lab|evo|air|epics?)\s*/\s*(pro|lab|evo|air|epics?)\b", re.IGNORECASE)
_SLASH_LIST_RE = re.compile(r"^([A-Za-z]{1,3}-)?([A-Za-z]?\d[\w.]*(?:\s*/\s*[A-Za-z]?\d[\w.]*)+)(.*)$")
_SENTENCE_SPLIT_RE = re.compile(r"[。；;！？!?\n]+|(?<=[a-z0-9\)])\.\s+")
_FOCAL_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*mm\b", re.IGNORECASE)
_ZOOM_RE = re.compile(r"(?<![\d.])(\d{1,3})\s*[-–~]\s*(\d{1,3})\s*mm\b", re.IGNORECASE)
# 截止符:CJK 全角区 / 括号 / 标点(品牌词后的型号串到这里为止)。
_CLIP_STOP_RE = re.compile("[\u3000-\u9fff\uff00-\uffef()\\[\\]{}<>,;!\"'|]")
_MOUNT_TOKEN_RE = re.compile(r"^(?:fe|e|z|xf|x|l|rf|ef|pl|m43)(?:-?mount)?$", re.IGNORECASE)
_HYPHEN_CODE_RE = re.compile(r"\b[A-Za-z]{1,3}-[A-Za-z]{1,2}\b")
_SERIES_WORDS = ("lab", "evo", "pro", "air", "chip", "epic", "vintage", "macro", "cine", "anamorphic")
_MOUNT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("PL-mount", r"\bpl(?:[- ]?mount)?\b"),
    ("RF-mount", r"\brf(?:[- ]?mount)?\b"),
    ("EF-mount", r"\bef(?:[- ]?mount)?\b"),
    ("X-mount", r"\b(?:xf|x)(?:[- ]?mount)?\b|富士|fuji"),
    ("FE-mount", r"\b(?:fe|e)(?:[- ]?mount)?\b|索尼|sony"),
    ("Z-mount", r"\bz(?:[- ]?mount)?\b|尼康|nikon"),
    ("L-mount", r"\bl(?:[- ]?mount)?\b"),
    ("M43", r"m4/?3|松下|panasonic|olympus"),
)
_MODALITY_CUES: dict[str, re.Pattern[str]] = {
    "text": re.compile(
        r"字幕|标题|文字|标注|标明|标识|标示|水印|\bui\b|文本|简介|贴纸|角标|左下角|右下角|caption|subtitle|title|overlay|on-?screen text|watermark|description|label",
        re.IGNORECASE,
    ),
    "visual": re.compile(
        r"特写|出镜|出现|展示|手持|画面|可见|开箱|实拍|镜头特写|搭载|装在|挂在|拿着|示范|演示|露出|贯穿|上手|外观|"
        r"close-?ups?|shown|appears?|visible|on[- ]screen|unbox|holding|mounted|footage|b-?roll|showcase|displayed|seen",
        re.IGNORECASE,
    ),
    "voice": re.compile(
        r"口播|口述|旁白|讲解|说到|说明|介绍|念出|谈到|口头|语音|narrat|voice|spoken|says?|said|talks?|explain|discuss",
        re.IGNORECASE,
    ),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_result(value: Any) -> dict[str, Any]:
    """compat 把 jsonb 读回成字符串;这里统一成 dict。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def final_payload(result: Any) -> dict[str, Any]:
    root = load_result(result)
    nested = _as_dict(root.get(FINAL_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")):
        return nested
    raw_nested = _as_dict(_as_dict(root.get("raw_gemini_video")).get(FINAL_DERIVE_METHOD))
    if not _as_dict(root.get("layer1_visual_content")) and _as_dict(raw_nested.get("layer1_visual_content")):
        return raw_nested
    return root


def _flatten_strings(value: Any, limit: int = 40) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [value]
    while stack and len(out) < limit:
        current = stack.pop()
        if isinstance(current, str):
            if current.strip():
                out.append(current)
        elif isinstance(current, dict):
            stack.extend(list(current.values())[::-1])
        elif isinstance(current, list):
            stack.extend(current[::-1])
    return out


def source_texts(result: Any) -> list[tuple[str, str]]:
    """(source_field, text) 列表;只取会点名产品的字段,不吞整份结果。"""

    payload = final_payload(result)
    root = load_result(result)
    raw = _as_dict(root.get("raw_gemini_video"))
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer4 = _as_dict(payload.get("layer4_attribution"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    pairs: list[tuple[str, str]] = []

    def add(field_name: str, value: Any) -> None:
        for text in _flatten_strings(value):
            pairs.append((field_name, text))

    add("product_presence", layer1.get("product_presence"))
    add("brand_exposure", layer1.get("brand_exposure"))
    add("content_summary", layer1.get("content_summary"))
    for scene in _as_list(layer1.get("scene_timeline")):
        if isinstance(scene, dict):
            add("scene_timeline", scene.get("what"))
    add("product_contribution", layer4.get("product_contribution"))
    for item in _as_list(layer4.get("attribution_breakdown")):
        if isinstance(item, dict):
            add("attribution_breakdown", item.get("evidence"))
    scores = _as_dict(layer6.get("scores"))
    add("product_proof_score", _as_dict(scores.get("product_proof_score")).get("evidence"))
    add("key_hook", layer6.get("key_hook"))
    add("final_verdict", layer6.get("final_verdict"))
    add("raw_content_topic", raw.get("content_topic"))
    add("raw_viltrox_lens", raw.get("viltrox_lens"))
    add("raw_viltrox_products_all", raw.get("viltrox_products_all"))
    return pairs


# ── 提及截取 ────────────────────────────────────────────────────────────────


def _clip(after_brand: str) -> tuple[str, list[str]]:
    """品牌词之后逐 token 吃型号词,遇到非型号词 / CJK / 标点即停。

    返回 (型号正文, 仅系列码列表):正文非空 = 型号提及;正文空但系列码非空 = 仅系列提及
    (「Viltrox Pro 系列」「Viltrox Epics」);两者皆空 = 没点名产品。
    """

    window = rewrite_mount_phrases(after_brand)
    window = _SERIES_SLASH_RE.sub(r"\1 \2", window)
    head = _CLIP_STOP_RE.split(window, maxsplit=1)[0]
    words = head.split()
    tokens: list[str] = []
    consumed = 0
    for raw_token in words:
        token = raw_token.strip(".,;:-/")
        if not token or not _TOKEN_RE.match(token):
            break
        tokens.append(token)
        consumed += 1
        if len(tokens) >= 8:
            break
    # 「... for Sony E-mount」写法:卡口在 for 之后,补成一个卡口 token(只认品牌/卡口词)。
    rest = " ".join(words[consumed:consumed + 4]).lower()
    if tokens and rest.startswith("for "):
        mount_hint = _mount_hint(rest)
        if mount_hint and not any(_MOUNT_TOKEN_RE.match(tok) for tok in tokens):
            tokens.append(mount_hint)
    while tokens and tokens[-1].lower() in _TRAILING_DROP:
        tokens.pop()
    while tokens and tokens[0].lower() in _LEADING_DROP:
        tokens.pop(0)
    # 仅系列:截出来的全是系列词(Pro / LAB / Epics …),没有任何型号码。
    series_codes = series_only_codes(" ".join(tokens)) if tokens else []
    if series_codes and not any(any(ch.isdigit() for ch in tok) for tok in tokens):
        return "", series_codes
    # 「Viltrox Pro 75mm F1.2」:前置系列词挪到尾部。
    leading_series: list[str] = []
    while tokens and tokens[0].lower() in _LEADING_SERIES:
        leading_series.append(tokens.pop(0))
    tokens.extend(leading_series)
    # 「DC-A1 7英寸」截出的尾部裸整数不是型号(但「35 1.8」这种焦段+光圈口语保留给 canonical)。
    while len(tokens) > 1 and re.fullmatch(r"\d{1,4}", tokens[-1]) and not re.fullmatch(r"\d\.\d", tokens[-1]):
        if len(tokens) == 2 and re.fullmatch(r"\d{1,3}", tokens[0]):
            break
        # 「Z-mount 85」「Pro 85」:其余 token 都不带数字时,裸整数就是焦段。
        if not any(any(ch.isdigit() for ch in tok) for tok in tokens[:-1]) and 5 <= int(tokens[-1]) <= 400:
            tokens[-1] = f"{tokens[-1]}mm"
            break
        tokens.pop()
    body = canonical_text(" ".join(tokens))
    if not _looks_like_product(body):
        return "", []
    return body[:80], []


def _clip_body(after_brand: str) -> str:
    return _clip(after_brand)[0]


def split_slash_list(body: str) -> list[str]:
    """「13mm/23mm/27mm/75mm Pro」→ 四条;「DC-X2/X3」→ DC-X2 / DC-X3;非列表原样单条。"""

    match = _SLASH_LIST_RE.match(_text(body))
    if not match:
        return [body]
    prefix, group, suffix = match.group(1) or "", match.group(2), match.group(3) or ""
    items = [item.strip() for item in re.split(r"\s*/\s*", group) if item.strip()]
    if len(items) < 2:
        return [body]
    out: list[str] = []
    for item in items:
        piece = f"{prefix}{item}{suffix}".strip()
        if piece and piece not in out:
            out.append(piece)
    return out[:6] or [body]


def canonical_text(value: str) -> str:
    """把口语/旧目录写法统一到「75mm F1.8」形态(不改系列/卡口词)。"""

    text = _text(value)
    text = re.sub(r"\bm\s*4\s*/\s*3\b", "M43", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![\d.])(\d{1,3})\s*/\s*(\d(?:\.\d)?)(?![\d])", r"\1mm F\2", text)
    text = re.sub(r"(?<![\d.])(\d{1,3})\s+(\d\.\d)(?![\d])", lambda m: f"{m.group(1)}mm F{m.group(2)}" if 5 <= int(m.group(1)) <= 400 else m.group(0), text)
    text = re.sub(r"(\d{1,3})\s*mm\s+(\d\.\d)(?![\d])", r"\1mm F\2", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![\d.])(\d{2,3})\s+(pro|air|evo|lab|ii)\b", r"\1mm \2", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([ft])\s*/\s*(\d)", r"\1\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([ft])(\d)(?![\d.])", r"\1\2.0", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d)\s*mm\b", r"\1mm", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_product(body: str) -> bool:
    if not body:
        return False
    if re.search(r"\d{1,3}mm\b", body, re.IGNORECASE):
        return True
    if _HYPHEN_CODE_RE.search(body):
        return True
    # 字母+数字型号码(K60 / Z1 / S05 / DC-A1),纯光圈(F1.4)或裸数字不算产品。
    stripped = re.sub(r"\b[ft]\d(?:\.\d)?\b", "", body, flags=re.IGNORECASE)
    return bool(re.search(r"\b[A-Za-z]{1,3}-?[A-Za-z]?\d", stripped))


def _mount_hint(text: str) -> str:
    low = text.lower()
    if "sony" in low or "e-mount" in low or " e mount" in low:
        return "FE"
    if "nikon" in low or "z-mount" in low:
        return "Z"
    if "fuji" in low or "x-mount" in low:
        return "X"
    if "leica" in low or "l-mount" in low:
        return "L"
    if "canon" in low and "rf" in low:
        return "RF"
    if "canon" in low and "ef" in low:
        return "EF"
    if "m43" in low or "m4/3" in low or "panasonic" in low or "olympus" in low:
        return "M43"
    return ""


def normalize_mention(value: str) -> str:
    text = canonical_text(_text(value).replace("full- frame", "full frame")).lower()
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    tokens = [token for token in text.split() if token not in {"af", "lens", "mount", "full", "frame", "apsc", "aps", "c"}]
    return " ".join(tokens)


def _sentence_for(text: str, start: int, end: int) -> str:
    pieces = [(m.start(), m.end()) for m in _SENTENCE_SPLIT_RE.finditer(text)]
    left = 0
    right = len(text)
    for s_start, s_end in pieces:
        if s_end <= start:
            left = s_end
        elif s_start >= end:
            right = s_start
            break
    return text[left:right]


def detect_modalities(context: str) -> list[str]:
    found = [name for name in ("text", "visual", "voice") if _MODALITY_CUES[name].search(context or "")]
    return found or ["unspecified"]


@dataclass
class Mention:
    text: str
    norm: str
    modalities: set[str] = field(default_factory=set)
    source_fields: set[str] = field(default_factory=set)
    count: int = 0
    series: str = ""
    from_list: bool = False


_CONTEXT_WHOLE_FIELDS = frozenset({"product_presence", "brand_exposure", "raw_viltrox_lens", "raw_viltrox_products_all"})


def extract_mentions(result: Any) -> list[Mention]:
    """从一份 final_v1 结果抽 Viltrox 产品提及(按 mention_norm 去重,合并线索)。

    型号提及:品牌词锚定 + token 截取(斜杠列表拆成多条);
    仅系列提及:只从产品出镜类字段(SERIES_SOURCE_FIELDS)里取,norm=系列码,series 字段标记。
    """

    found: dict[str, Mention] = {}

    def record(norm: str, body: str, context: str, field_name: str, series: str = "", from_list: bool = False) -> None:
        item = found.get(norm)
        if item is None:
            item = Mention(text=body, norm=norm, series=series, from_list=from_list)
            found[norm] = item
        elif len(body) > len(item.text) and not series:
            item.text = body
        item.modalities.update(detect_modalities(context))
        item.source_fields.add(field_name)
        item.count += 1

    for field_name, text in source_texts(result):
        for match in _BRAND_RE.finditer(text):
            body, series_codes = _clip(text[match.end():match.end() + 120])
            if not body and not series_codes:
                continue
            context = text if field_name in _CONTEXT_WHOLE_FIELDS else _sentence_for(text, match.start(), match.end() + len(body or "") + 8)
            if not body:
                if field_name not in SERIES_SOURCE_FIELDS:
                    continue
                for code in series_codes:
                    record(f"{SERIES_KEY_PREFIX}{code.lower()}", series_label(code), context, field_name, series=code)
                continue
            pieces = split_slash_list(body)
            for piece in pieces:
                norm = normalize_mention(piece)
                if norm:
                    record(norm, piece, context, field_name, from_list=len(pieces) > 1)
            if len(found) >= MAX_MENTIONS_PER_CACHE * 2:
                break
    for item in found.values():
        if len(item.modalities) > 1:
            item.modalities.discard("unspecified")
    return list(found.values())


# ── 目录归一 ────────────────────────────────────────────────────────────────


def _normkey(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


def parse_spec(value: str) -> dict[str, Any]:
    text = canonical_text(_text(value))
    low = text.lower()
    zoom = _ZOOM_RE.search(low)
    if zoom:
        focal = sorted({int(zoom.group(1)), int(zoom.group(2))})
    else:
        focal = sorted({int(m) for m in _FOCAL_RE.findall(low)})
    aperture = ""
    aperture_kind = ""
    ap = re.search(r"\b([ft])\s*/?\s*(\d(?:\.\d)?)\b", low)
    if ap:
        aperture = ap.group(2)
        aperture_kind = ap.group(1)
    elif focal:
        bare = re.search(r"\d{1,3}\s*mm\s+(\d\.\d)\b", low)
        if bare:
            aperture = bare.group(1)
    if aperture and "." not in aperture:
        aperture = f"{aperture}.0"
    series = [word for word in _SERIES_WORDS if re.search(rf"(?<![a-z]){word}(?![a-z])", low)]
    mount = ""
    for code, pattern in _MOUNT_PATTERNS:
        if re.search(pattern, low):
            mount = code
            break
    return {"focal": focal, "aperture": aperture, "aperture_kind": aperture_kind, "series": series, "mount": mount}


_FAMILY_SERIES_SUFFIX = {"pro", "ii", "iii", "plus", "max", "ultra", "air", "evo", "lab"}
_FAMILY_MOUNT_TAIL_RE = re.compile(r"\s+(?:FE|E|Z|XF|X|EF|RF|L|DL|PL|M43|M)$")


def family_name(model_name: str) -> str:
    """型号名 → 家族展示名:镜头去卡口/画幅后缀(AF 75mm F1.8 EVO);
    非镜头取到首个带数字的型号码为止(Vintage Z2 / DC-A1 / K60),吸收 Pro/II 等后缀。"""

    text = _text(model_name)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\bviltrox\b", "", text, flags=re.IGNORECASE)
    text = canonical_text(text)
    if re.search(r"\d{1,3}mm\b", text, re.IGNORECASE):
        text = re.sub(r"\bfor\b.*$", "", text, flags=re.IGNORECASE)
        text = re.sub(r"full-?\s*frame|aps-?c|\blens\b|\bcamera\b|\bset\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip(" -,")
        while True:
            trimmed = _FAMILY_MOUNT_TAIL_RE.sub("", text)
            if trimmed == text:
                break
            text = trimmed
        return text[:80]
    words = text.split()
    kept: list[str] = []
    seen_code = False
    for word in words:
        if seen_code:
            if word.lower().strip("-") in _FAMILY_SERIES_SUFFIX:
                kept.append(word)
                continue
            break
        kept.append(word)
        if any(ch.isdigit() for ch in word):
            seen_code = True
    if not seen_code:
        kept = words
    family = " ".join(kept).strip(" -,")
    family = re.sub(r"[\s\-]+[A-Za-z]$", "", family) if len(kept) > 1 and seen_code else family
    return family[:80]


def _mount_code(mount: str) -> str:
    norm = _text(mount).lower()
    if not norm:
        return ""
    for code, _pattern in _MOUNT_PATTERNS:
        if norm.startswith(code.lower().split("-")[0]):
            return code
    if "fe" in norm or norm.startswith("e"):
        return "FE-mount"
    return _text(mount)


@dataclass
class CatalogProduct:
    sku: str
    model_name: str
    marketing_name: str
    category_main: str
    series: str
    mount: str
    family: str
    family_key: str
    spec: dict[str, Any]
    token_key: str


class CatalogIndex:
    """一次装目录(vkpi_products + 别名),内存里反复归一。"""

    def __init__(self, products: Iterable[dict[str, Any]], aliases: Iterable[dict[str, Any]] | None = None) -> None:
        self.products: dict[str, CatalogProduct] = {}
        self.alias_map: dict[str, set[str]] = {}
        for row in products:
            sku = _text(row.get("sku")).upper()
            if not sku or sku.startswith("IMAGE-AWARDS"):
                continue
            model_name = _text(row.get("model_name"))
            family = family_name(model_name) or sku
            product = CatalogProduct(
                sku=sku,
                model_name=model_name,
                marketing_name=_text(row.get("marketing_name")),
                category_main=_text(row.get("category_main")),
                series=_text(row.get("series")),
                mount=_mount_code(_text(row.get("mount"))),
                family=family,
                family_key=_normkey(family),
                spec=parse_spec(f"{model_name} {row.get('mount') or ''}"),
                token_key=_normkey(model_name),
            )
            self.products[sku] = product
            self.alias_map.setdefault(_normkey(sku), set()).add(sku)
            self.alias_map.setdefault(_normkey(model_name), set()).add(sku)
            self.alias_map.setdefault(_normkey(family), set()).add(sku)
        alias_rows = list(aliases) if aliases is not None else []
        if not alias_rows:
            for row in products:
                alias_rows.extend(generated_aliases_for_product(dict(row)))
        for row in alias_rows:
            sku = _text(row.get("sku")).upper()
            key = _normkey(row.get("alias_norm") or normalize_alias(row.get("alias")))
            if sku in self.products and key:
                self.alias_map.setdefault(key, set()).add(sku)

    def _outcome(self, skus: set[str], mention: str, *, force_family: bool = False, note: str = "") -> dict[str, Any]:
        # 旧成本目录(VL-xxx)是官方目录的重复登记(无系列/卡口);两边都命中时只认官方行。
        official = {sku for sku in skus if not sku.upper().startswith("VL-")}
        candidates = sorted(official or skus)[:MAX_CANDIDATES]
        if not candidates:
            return {"resolution": "unresolved", "product_sku": None, "lens_key": "", "display_name": mention, "category_main": "", "candidate_skus": [], "note": note}
        if len(candidates) == 1 and not force_family:
            product = self.products[candidates[0]]
            return {"resolution": "sku", "product_sku": product.sku, "lens_key": product.family_key, "display_name": product.family, "category_main": product.category_main, "candidate_skus": candidates, "note": note}
        families = {self.products[sku].family_key for sku in candidates}
        if len(families) == 1:
            product = self.products[candidates[0]]
            return {"resolution": "family", "product_sku": None, "lens_key": product.family_key, "display_name": product.family, "category_main": product.category_main, "candidate_skus": candidates, "note": note}
        return {"resolution": "unresolved", "product_sku": None, "lens_key": "", "display_name": mention, "category_main": "", "candidate_skus": candidates, "note": note}

    def series_outcome(self, code: str) -> dict[str, Any]:
        """仅系列提及:归 family(系列键 series:xxx + 该系列候选 SKU,上限 MAX_CANDIDATES),不猜型号。"""

        wanted = _text(code).lower()
        skus = sorted(
            p.sku for p in self.products.values()
            if not p.sku.startswith("VL-") and (wanted in p.spec["series"] or _text(p.series).lower() == wanted)
        )
        categories = {self.products[sku].category_main for sku in skus}
        return {
            "resolution": "family" if skus else "unresolved",
            "product_sku": None,
            "lens_key": f"{SERIES_KEY_PREFIX}{wanted}",
            "display_name": series_label(code),
            "category_main": next(iter(categories)) if len(categories) == 1 else "",
            "candidate_skus": skus[:MAX_CANDIDATES],
            "note": "series_only",
        }

    def _apply_mount(self, pool: list[CatalogProduct], mount: str) -> tuple[list[CatalogProduct], bool]:
        """卡口硬约束;目录没有该卡口时退回家族级(force_family),绝不猜 SKU。"""

        if not mount or not pool:
            return pool, False
        wanted = {mount}
        if mount in {"PL-mount", "L-mount"}:
            wanted = {"PL-mount", "L-mount"}
        narrowed = [p for p in pool if p.mount in wanted]
        if narrowed:
            return narrowed, False
        return pool, True

    def _resolve_spec(self, mention_text: str, spec: dict[str, Any], hit: set[str]) -> dict[str, Any]:
        pool = [p for p in self.products.values() if p.spec["focal"] == spec["focal"]]
        # 别名命中的同焦段行并入候选池,再统一过硬约束(别名不能绕过光圈/系列/卡口)。
        seen = {p.sku for p in pool}
        pool.extend(
            self.products[sku]
            for sku in sorted(hit)
            if sku in self.products and sku not in seen and self.products[sku].spec["focal"] == spec["focal"]
        )
        if spec["aperture"]:
            pool = [p for p in pool if p.spec["aperture"] == spec["aperture"]]
            # F 光圈(摄影镜头)与 T 光圈(电影镜头)不互认。
            if spec["aperture_kind"]:
                pool = [p for p in pool if not p.spec["aperture_kind"] or p.spec["aperture_kind"] == spec["aperture_kind"]]
        for word in spec["series"]:
            pool = [p for p in pool if word in p.spec["series"]]
        pool, mount_unmatched = self._apply_mount(pool, spec["mount"])
        official = [p for p in pool if not p.sku.startswith("VL-")]
        if official:
            pool = official
        if not spec["series"] and len({p.family_key for p in pool}) > 1:
            # 提及没写系列时,只认无系列后缀的家族(28mm F4.5 vs 28mm F4.5 Chip 不乱猜)。
            plain = [p for p in pool if not p.spec["series"]]
            if plain and len({p.family_key for p in plain}) == 1:
                pool = plain
        return self._outcome({p.sku for p in pool}, mention_text, force_family=mount_unmatched, note="mount_unmatched" if mount_unmatched else "")

    def _resolve_alias(self, mention_text: str, canonical: str, spec: dict[str, Any]) -> dict[str, Any]:
        """口语别名 → canonical 家族名;再按原提及里的卡口收窄。canonical 不在目录 = 照旧 unresolved。"""

        hit = set(self.alias_map.get(_normkey(canonical)) or ())
        hit |= set(self.alias_map.get(_normkey(family_name(canonical))) or ())
        # 非镜头(适配器 / 灯 / 监视器)的型号码本身就含 EF / PL / E 字样,卡口约束只对镜头生效。
        mount = spec["mount"] if parse_spec(canonical)["focal"] else ""
        if hit:
            pool, mount_unmatched = self._apply_mount([self.products[sku] for sku in sorted(hit)], mount)
            outcome = self._outcome({p.sku for p in pool}, mention_text, force_family=mount_unmatched, note="mount_unmatched" if mount_unmatched else "")
        else:
            canonical_spec = parse_spec(canonical)
            if not canonical_spec["focal"]:
                return self._outcome(set(), mention_text)
            canonical_spec["mount"] = mount
            outcome = self._resolve_spec(mention_text, canonical_spec, set())
        outcome["note"] = ";".join(part for part in (outcome.get("note"), "alias_table") if part)
        return outcome

    def resolve(self, mention: str) -> dict[str, Any]:
        mention_text = _text(mention)
        if not mention_text:
            return self._outcome(set(), mention_text)
        # 1. 别名 / 家族名精确命中(镜头还要并上规格匹配,别让旧目录别名抢先定案)
        key = _normkey(mention_text)
        hit = set(self.alias_map.get(key) or self.alias_map.get(_normkey(normalize_alias(mention_text))) or ())
        spec = parse_spec(mention_text)
        if hit and not spec["focal"]:
            return self._outcome(hit, mention_text)
        # 2. 口语别名表(数据驱动):「85 1.4」「135 LAB」「Z2」→ canonical 家族,再过目录
        canonical = lookup_lens_alias(mention_text)
        if canonical:
            outcome = self._resolve_alias(mention_text, canonical, spec)
            if outcome["resolution"] != "unresolved":
                return outcome
        # 3. 镜头规格匹配:焦段必须全等;光圈 / 系列 / 卡口有就当硬约束
        if spec["focal"]:
            return self._resolve_spec(mention_text, spec, hit)
        # 4. 非镜头型号码(DC-A1 / Z1 Pro / K60):型号 token 子串命中
        probe = key
        if len(probe) >= 3:
            pool = [p for p in self.products.values() if probe and (probe in p.token_key or probe == _normkey(p.sku))]
            if pool:
                exact = [p for p in pool if p.token_key == probe or _normkey(p.sku) == probe]
                return self._outcome({p.sku for p in (exact or pool)}, mention_text)
        return self._outcome(set(), mention_text)


def load_catalog_index(conn: Any) -> CatalogIndex:
    products = [dict(row) for row in conn.execute(
        "SELECT sku, model_name, marketing_name, category_main, series, mount, specs_json, fit_tags_json, product_url FROM vkpi_products"
    ).fetchall()]
    aliases: list[dict[str, Any]] = []
    try:
        aliases = [dict(row) for row in conn.execute(
            "SELECT sku, alias, alias_norm FROM vkpi_product_aliases"
        ).fetchall()]
    except Exception:
        aliases = []
    return CatalogIndex(products, aliases or None)


def extract_resolved(result: Any, index: CatalogIndex) -> list[dict[str, Any]]:
    """抽取 + 归一;同家族 / 同 SKU 的多次提及合并成一行(unresolved 按原文去重)。"""

    merged: dict[str, dict[str, Any]] = {}
    for mention in extract_mentions(result):
        outcome = index.series_outcome(mention.series) if mention.series else index.resolve(mention.text)
        if outcome["resolution"] == "unresolved" and mention.from_list:
            # 「13mm/23mm/27mm/75mm Pro」:列表尾巴的系列词未必属于每一项,去掉再试一次(仍过目录)。
            bare = re.sub(r"\b(?:pro|air|evo|lab|ii)\b", " ", mention.text, flags=re.IGNORECASE).strip()
            if bare and bare != mention.text:
                retry = index.resolve(canonical_text(bare))
                if retry["resolution"] != "unresolved":
                    outcome = retry
                    outcome["note"] = ";".join(part for part in (outcome.get("note"), "list_suffix_dropped") if part)
        group_key = (
            f"sku:{outcome['product_sku']}" if outcome["resolution"] == "sku"
            else f"family:{outcome['lens_key']}" if outcome["resolution"] == "family"
            else f"raw:{mention.norm}"
        )
        row = merged.get(group_key)
        if row is None:
            row = {
                "mention_text": mention.text,
                "mention_norm": mention.norm,
                "resolution": outcome["resolution"],
                "product_sku": outcome["product_sku"],
                "lens_key": outcome["lens_key"],
                "display_name": outcome["display_name"],
                "category_main": outcome["category_main"],
                "candidate_skus": list(outcome["candidate_skus"]),
                "modalities": set(),
                "source_fields": set(),
                "mention_count": 0,
                "note": _text(outcome.get("note")),
            }
            merged[group_key] = row
        row["modalities"].update(mention.modalities)
        row["source_fields"].update(mention.source_fields)
        row["mention_count"] += mention.count
        if len(merged) >= MAX_MENTIONS_PER_CACHE:
            break
    # 同一条结果里「85mm」「Z2」这类残缺提及,若已有同焦段 / 同型号码的归一行,并进去。
    resolved_rows = [row for row in merged.values() if row["resolution"] != "unresolved"]
    for key in [k for k in merged if k.startswith("raw:")]:
        row = merged[key]
        tokens = set(row["mention_norm"].split())
        host = None
        for candidate in resolved_rows:
            host_tokens = set(normalize_mention(candidate["display_name"]).split()) | set(candidate["mention_norm"].split())
            if tokens and tokens <= host_tokens:
                host = candidate
                break
        if host is None:
            continue
        host["modalities"].update(row["modalities"])
        host["source_fields"].update(row["source_fields"])
        host["mention_count"] += row["mention_count"]
        del merged[key]
    out: list[dict[str, Any]] = []
    for row in merged.values():
        modalities = {m for m in row["modalities"] if m in MODALITIES}
        if len(modalities) > 1:
            modalities.discard("unspecified")
        row["modalities"] = sorted(modalities or {"unspecified"})
        row["source_fields"] = sorted(row["source_fields"])
        row["v_relevance"], row["v_reason"] = v_relevance_for(row)
        out.append(row)
    return out


# ── v_relevance 三态投影(纯函数,读侧 / 回填共用) ────────────────────────────


def v_relevance_for(row: dict[str, Any]) -> tuple[str, str]:
    """单条提及行 → (v_relevance, v_reason)。只看落表字段(resolution / lens_key / modalities /
    source_fields),回填统计与读侧端点共用同一口径;目录无该卡口退回家族级(note=mount_unmatched)
    仍按家族归一计。"""

    resolution = _text(row.get("resolution"))
    lens_key = _text(row.get("lens_key"))
    modalities = {m for m in (row.get("modalities") or []) if m in MODALITIES}
    sources = {item for item in (row.get("source_fields") or []) if item}
    if lens_key.startswith(SERIES_KEY_PREFIX):
        return "likely", "series_only"
    if resolution not in {"sku", "family"}:
        return "likely", "unresolved_mention"
    if sources and sources <= ADVISORY_FIELDS:
        return "likely", "advisory_field_only"
    if modalities & EXPLICIT_MODALITIES:
        return "confirmed", "catalog_match_explicit_modality"
    return "likely", "catalog_match_modality_unspecified"


def explain(result: Any, index: CatalogIndex) -> dict[str, Any]:
    """--cache-id 对照用的抽取轨迹:每个品牌锚点截到了什么、归一成什么。"""

    anchors: list[dict[str, Any]] = []
    for field_name, text in source_texts(result):
        for match in _BRAND_RE.finditer(text):
            window = text[match.end():match.end() + 120]
            body, series_codes = _clip(window)
            anchors.append({
                "field": field_name,
                "after": window[:60],
                "body": body,
                "series": series_codes,
                "pieces": split_slash_list(body) if body else [],
            })
            if len(anchors) >= 80:
                break
    rows = extract_resolved(result, index)
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "alias_table_version": ALIAS_TABLE_VERSION,
        "anchors": anchors,
        "rows": [
            {k: row[k] for k in ("mention_text", "mention_norm", "resolution", "product_sku", "lens_key", "display_name", "candidate_skus", "modalities", "source_fields", "mention_count", "v_relevance", "v_reason", "note")}
            for row in rows
        ],
    }


__all__ = [
    "ADVISORY_FIELDS",
    "ALIAS_TABLE_VERSION",
    "EXTRACTOR_VERSION",
    "canonical_text",
    "FINAL_DERIVE_METHOD",
    "MODALITIES",
    "RESOLUTIONS",
    "SERIES_KEY_PREFIX",
    "V_RELEVANCE",
    "V_RELEVANCE_LABELS",
    "CatalogIndex",
    "detect_modalities",
    "explain",
    "extract_mentions",
    "extract_resolved",
    "family_name",
    "load_catalog_index",
    "normalize_mention",
    "parse_spec",
    "source_texts",
    "split_slash_list",
    "v_relevance_for",
]
