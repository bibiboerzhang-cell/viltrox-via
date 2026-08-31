"""lens_evidence 的型号截取/规格解析/目录零件(CC 战役 2026-08-30 平移,行为逐字节不变)。

内容:品牌词后型号 token 截取(_clip 全家)、口径归一(canonical_text)、规格解析
(parse_spec)、家族名(family_name)、CatalogProduct 构造与候选池收窄碎步。
红线原样继承:纯读、绝不杜撰型号、不触 viltrox_fit_score / rule_v0;
本模块不 import lens_evidence(防环)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domains.products.product_aliases_lens import (
    rewrite_mount_phrases,
    series_only_codes,
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
# _mount_hint 的判序与老 if 链逐字节一致:FE→Z→X→L→(canon rf/ef)→M43。
_MOUNT_HINT_NEEDLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("FE", ("sony", "e-mount", " e mount")),
    ("Z", ("nikon", "z-mount")),
    ("X", ("fuji", "x-mount")),
    ("L", ("leica", "l-mount")),
)
_FAMILY_SERIES_SUFFIX = {"pro", "ii", "iii", "plus", "max", "ultra", "air", "evo", "lab"}
_FAMILY_MOUNT_TAIL_RE = re.compile(r"\s+(?:FE|E|Z|XF|X|EF|RF|L|DL|PL|M43|M)$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normkey(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _text(value).lower())


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
    for code, needles in _MOUNT_HINT_NEEDLES:
        if any(needle in low for needle in needles):
            return code
    if "canon" in low and "rf" in low:
        return "RF"
    if "canon" in low and "ef" in low:
        return "EF"
    if any(needle in low for needle in ("m43", "m4/3", "panasonic", "olympus")):
        return "M43"
    return ""


# ── _clip 碎步 ──────────────────────────────────────────────────────────────


def _eat_model_tokens(words: list[str]) -> tuple[list[str], int]:
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
    return tokens, consumed


def _maybe_append_for_mount(tokens: list[str], words: list[str], consumed: int) -> None:
    """「... for Sony E-mount」写法:卡口在 for 之后,补成一个卡口 token(只认品牌/卡口词)。"""
    rest = " ".join(words[consumed:consumed + 4]).lower()
    if tokens and rest.startswith("for "):
        mount_hint = _mount_hint(rest)
        if mount_hint and not any(_MOUNT_TOKEN_RE.match(tok) for tok in tokens):
            tokens.append(mount_hint)


def _strip_edge_tokens(tokens: list[str]) -> None:
    while tokens and tokens[-1].lower() in _TRAILING_DROP:
        tokens.pop()
    while tokens and tokens[0].lower() in _LEADING_DROP:
        tokens.pop(0)


def _series_only_tokens(tokens: list[str]) -> list[str]:
    """仅系列:截出来的全是系列词(Pro / LAB / Epics …),没有任何型号码。"""
    series_codes = series_only_codes(" ".join(tokens)) if tokens else []
    if series_codes and not any(any(ch.isdigit() for ch in tok) for tok in tokens):
        return series_codes
    return []


def _rotate_leading_series(tokens: list[str]) -> None:
    """「Viltrox Pro 75mm F1.2」:前置系列词挪到尾部。"""
    leading_series: list[str] = []
    while tokens and tokens[0].lower() in _LEADING_SERIES:
        leading_series.append(tokens.pop(0))
    tokens.extend(leading_series)


def _bare_int_is_focal(tokens: list[str]) -> bool:
    # 「Z-mount 85」「Pro 85」:其余 token 都不带数字时,裸整数就是焦段。
    if any(any(ch.isdigit() for ch in tok) for tok in tokens[:-1]):
        return False
    return 5 <= int(tokens[-1]) <= 400


def _trim_trailing_bare_ints(tokens: list[str]) -> None:
    """「DC-A1 7英寸」截出的尾部裸整数不是型号(但「35 1.8」这种焦段+光圈口语保留给 canonical)。"""
    while len(tokens) > 1 and re.fullmatch(r"\d{1,4}", tokens[-1]) and not re.fullmatch(r"\d\.\d", tokens[-1]):
        if len(tokens) == 2 and re.fullmatch(r"\d{1,3}", tokens[0]):
            break
        if _bare_int_is_focal(tokens):
            tokens[-1] = f"{tokens[-1]}mm"
            break
        tokens.pop()


def _clip(after_brand: str) -> tuple[str, list[str]]:
    """品牌词之后逐 token 吃型号词,遇到非型号词 / CJK / 标点即停。

    返回 (型号正文, 仅系列码列表):正文非空 = 型号提及;正文空但系列码非空 = 仅系列提及
    (「Viltrox Pro 系列」「Viltrox Epics」);两者皆空 = 没点名产品。
    """

    window = rewrite_mount_phrases(after_brand)
    window = _SERIES_SLASH_RE.sub(r"\1 \2", window)
    head = _CLIP_STOP_RE.split(window, maxsplit=1)[0]
    words = head.split()
    tokens, consumed = _eat_model_tokens(words)
    _maybe_append_for_mount(tokens, words, consumed)
    _strip_edge_tokens(tokens)
    series_codes = _series_only_tokens(tokens)
    if series_codes:
        return "", series_codes
    _rotate_leading_series(tokens)
    _trim_trailing_bare_ints(tokens)
    body = canonical_text(" ".join(tokens))
    if not _looks_like_product(body):
        return "", []
    return body[:80], []


def _slash_pieces(prefix: str, items: list[str], suffix: str) -> list[str]:
    out: list[str] = []
    for item in items:
        piece = f"{prefix}{item}{suffix}".strip()
        if piece and piece not in out:
            out.append(piece)
    return out


def split_slash_list(body: str) -> list[str]:
    """「13mm/23mm/27mm/75mm Pro」→ 四条;「DC-X2/X3」→ DC-X2 / DC-X3;非列表原样单条。"""

    match = _SLASH_LIST_RE.match(_text(body))
    if not match:
        return [body]
    prefix, group, suffix = match.group(1) or "", match.group(2), match.group(3) or ""
    items = [item.strip() for item in re.split(r"\s*/\s*", group) if item.strip()]
    if len(items) < 2:
        return [body]
    out = _slash_pieces(prefix, items, suffix)
    return out[:6] or [body]


# ── 规格解析 / 家族名 ─────────────────────────────────────────────────────────


def _parse_aperture(low: str, focal: list[int]) -> tuple[str, str]:
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
    return aperture, aperture_kind


def parse_spec(value: str) -> dict[str, Any]:
    text = canonical_text(_text(value))
    low = text.lower()
    zoom = _ZOOM_RE.search(low)
    if zoom:
        focal = sorted({int(zoom.group(1)), int(zoom.group(2))})
    else:
        focal = sorted({int(m) for m in _FOCAL_RE.findall(low)})
    aperture, aperture_kind = _parse_aperture(low, focal)
    series = [word for word in _SERIES_WORDS if re.search(rf"(?<![a-z]){word}(?![a-z])", low)]
    mount = ""
    for code, pattern in _MOUNT_PATTERNS:
        if re.search(pattern, low):
            mount = code
            break
    return {"focal": focal, "aperture": aperture, "aperture_kind": aperture_kind, "series": series, "mount": mount}


def _lens_family(text: str) -> str:
    text = re.sub(r"\bfor\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"full-?\s*frame|aps-?c|\blens\b|\bcamera\b|\bset\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -,")
    while True:
        trimmed = _FAMILY_MOUNT_TAIL_RE.sub("", text)
        if trimmed == text:
            break
        text = trimmed
    return text[:80]


def _code_family_tokens(words: list[str]) -> tuple[list[str], bool]:
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
    return kept, seen_code


def family_name(model_name: str) -> str:
    """型号名 → 家族展示名:镜头去卡口/画幅后缀(AF 75mm F1.8 EVO);
    非镜头取到首个带数字的型号码为止(Vintage Z2 / DC-A1 / K60),吸收 Pro/II 等后缀。"""

    text = _text(model_name)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\bviltrox\b", "", text, flags=re.IGNORECASE)
    text = canonical_text(text)
    if re.search(r"\d{1,3}mm\b", text, re.IGNORECASE):
        return _lens_family(text)
    words = text.split()
    kept, seen_code = _code_family_tokens(words)
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


# ── 目录零件 ────────────────────────────────────────────────────────────────


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


def catalog_product(row: dict[str, Any]) -> CatalogProduct | None:
    """一行 vkpi_products → CatalogProduct;旧图库行(IMAGE-AWARDS)/空 SKU 丢弃。"""
    sku = _text(row.get("sku")).upper()
    if not sku or sku.startswith("IMAGE-AWARDS"):
        return None
    model_name = _text(row.get("model_name"))
    family = family_name(model_name) or sku
    return CatalogProduct(
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


def merged_note(existing: Any, extra: str) -> str:
    return ";".join(part for part in (existing, extra) if part)


# ── 候选池收窄碎步(_resolve_spec)──────────────────────────────────────────


def focal_pool(products: dict[str, CatalogProduct], hit: set[str], spec: dict[str, Any]) -> list[CatalogProduct]:
    pool = [p for p in products.values() if p.spec["focal"] == spec["focal"]]
    # 别名命中的同焦段行并入候选池,再统一过硬约束(别名不能绕过光圈/系列/卡口)。
    seen = {p.sku for p in pool}
    pool.extend(
        products[sku]
        for sku in sorted(hit)
        if sku in products and sku not in seen and products[sku].spec["focal"] == spec["focal"]
    )
    return pool


def narrow_by_aperture(pool: list[CatalogProduct], spec: dict[str, Any]) -> list[CatalogProduct]:
    if spec["aperture"]:
        pool = [p for p in pool if p.spec["aperture"] == spec["aperture"]]
        # F 光圈(摄影镜头)与 T 光圈(电影镜头)不互认。
        if spec["aperture_kind"]:
            pool = [p for p in pool if not p.spec["aperture_kind"] or p.spec["aperture_kind"] == spec["aperture_kind"]]
    return pool


def narrow_by_series(pool: list[CatalogProduct], spec: dict[str, Any]) -> list[CatalogProduct]:
    for word in spec["series"]:
        pool = [p for p in pool if word in p.spec["series"]]
    return pool


def prefer_official(pool: list[CatalogProduct]) -> list[CatalogProduct]:
    official = [p for p in pool if not p.sku.startswith("VL-")]
    return official or pool


def prefer_plain_family(pool: list[CatalogProduct], spec: dict[str, Any]) -> list[CatalogProduct]:
    if not spec["series"] and len({p.family_key for p in pool}) > 1:
        # 提及没写系列时,只认无系列后缀的家族(28mm F4.5 vs 28mm F4.5 Chip 不乱猜)。
        plain = [p for p in pool if not p.spec["series"]]
        if plain and len({p.family_key for p in plain}) == 1:
            return plain
    return pool


def probe_hits(product: CatalogProduct, probe: str) -> bool:
    return bool(probe and (probe in product.token_key or probe == _normkey(product.sku)))


def probe_exact(product: CatalogProduct, probe: str) -> bool:
    return product.token_key == probe or _normkey(product.sku) == probe
