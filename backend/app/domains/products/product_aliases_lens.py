"""镜头出镜解析器的别名表(数据驱动,纯常量;零 DB、零 LLM)。

三张表,全部由 ``app.domains.kol.lens_evidence`` 消费:

* ``LENS_ALIASES``  —— 口语 / 简写 / 中文写法 → 目录家族或型号名(canonical)。
  canonical 必须是 vkpi_products 能归一出来的家族 / 型号写法;解析器拿到 canonical 后
  仍要过目录(焦段 / 光圈 / 系列 / 卡口硬约束),表里写错不会凭空造 SKU,只会落 unresolved。
* ``MOUNT_PHRASES`` —— 中英文卡口说法 → 规范卡口 token(「Z 卡口」「索尼口」「for Sony E」)。
  品牌词之后的截取窗口先做这一步改写,CJK 卡口词才不会被当截止符吃掉。
* ``SERIES_MARKERS`` —— 仅系列的提及(「Pro 系列」「Epics」「LAB 高端线」)→ 系列码。
  仅系列 = 提及但未归一到家族,投影成 v_relevance=likely;绝不猜具体型号。

真值边界:表里每一条的 canonical 都对应目录真实家族(2026-08 目录快照,见
``tests/test_lens_evidence_resolver.py`` 逐条核验);没把握的说法(如「25mm F1.8」
「45mm T1.5」「DC-K6」)故意不收——保留原文比杜撰强。
"""
from __future__ import annotations

import re
from typing import Iterable

ALIAS_TABLE_VERSION = "lens_aliases_2026_08_v1"

# (alias 写法, canonical 家族/型号, 说明) —— alias 经 alias_key() 归一后精确匹配。
LENS_ALIASES: tuple[tuple[str, str, str], ...] = (
    # ── Pro 系列(大光圈定焦) ────────────────────────────────────────────
    ("85 1.4", "AF 85mm F1.4 Pro", "口语焦段+光圈"),
    ("85 1.4 Pro", "AF 85mm F1.4 Pro", "口语+系列"),
    ("85mm f1.4 Pro", "AF 85mm F1.4 Pro", "全写"),
    ("AF85 F1.4", "AF 85mm F1.4 Pro", "AF 粘连写法"),
    ("85/1.4", "AF 85mm F1.4 Pro", "斜杠光圈"),
    ("75 1.2", "AF 75mm F1.2 Pro", "口语"),
    ("75 1.2 Pro", "AF 75mm F1.2 Pro", "口语+系列"),
    ("27 1.2", "AF 27mm F1.2 Pro", "口语"),
    ("27 1.2 Pro", "AF 27mm F1.2 Pro", "口语+系列"),
    ("56 1.2", "AF 56mm F1.2 Pro", "口语"),
    ("56 1.2 Pro", "AF 56mm F1.2 Pro", "口语+系列"),
    ("50 1.4", "AF 50mm F1.4 Pro", "口语"),
    ("50 1.4 Pro", "AF 50mm F1.4 Pro", "口语+系列"),
    # ── LAB 系列 ──────────────────────────────────────────────────────────
    ("135 LAB", "AF 135mm F1.8 LAB", "焦段+系列"),
    ("135 1.8", "AF 135mm F1.8 LAB", "口语(目录唯一 135/1.8 官方行)"),
    ("135 1.8 LAB", "AF 135mm F1.8 LAB", "口语+系列"),
    ("35 LAB", "AF 35mm F1.2 LAB", "焦段+系列"),
    ("35 1.2", "AF 35mm F1.2 LAB", "口语(目录唯一 35/1.2 官方行)"),
    ("35 1.2 LAB", "AF 35mm F1.2 LAB", "口语+系列"),
    # ── EVO 系列 ──────────────────────────────────────────────────────────
    ("75 EVO", "AF 75mm F1.8 EVO", "焦段+系列"),
    ("75 1.8", "AF 75mm F1.8 EVO", "口语"),
    ("85 EVO", "AF 85mm F2.0 EVO", "焦段+系列"),
    ("85 2.0", "AF 85mm F2.0 EVO", "口语"),
    ("85 F2", "AF 85mm F2.0 EVO", "整数光圈"),
    ("90 EVO", "AF 90mm F2.2 EVO", "焦段+系列"),
    ("90 2.2", "AF 90mm F2.2 EVO", "口语"),
    ("55 EVO", "AF 55mm F1.8 EVO", "焦段+系列"),
    ("55 1.8", "AF 55mm F1.8 EVO", "口语"),
    ("35 EVO", "AF 35mm F1.8 EVO", "焦段+系列"),
    ("35 1.8 EVO", "AF 35mm F1.8 EVO", "口语+系列"),
    ("26 EVO", "AF 26mm F2.8 EVO", "焦段+系列"),
    ("26 2.8", "AF 26mm F2.8 EVO", "口语"),
    ("26mm f2.8", "AF 26mm F2.8 EVO", "全写"),
    # ── Air 系列 ──────────────────────────────────────────────────────────
    ("9 Air", "AF 9mm F2.8 Air", "焦段+系列"),
    ("9mm 2.8", "AF 9mm F2.8 Air", "口语"),
    ("14 Air", "AF 14mm F4.0 Air", "焦段+系列"),
    ("14mm F4", "AF 14mm F4.0 Air", "整数光圈"),
    ("15 Air", "AF 15mm F1.7 Air", "焦段+系列"),
    ("15 1.7", "AF 15mm F1.7 Air", "口语"),
    ("20 Air", "AF 20mm F2.8 Air", "焦段+系列"),
    ("20 2.8", "AF 20mm F2.8 Air", "口语"),
    ("25 Air", "AF 25mm F1.7 Air", "焦段+系列"),
    ("25 1.7", "AF 25mm F1.7 Air", "口语"),
    ("35 Air", "AF 35mm F1.7 Air", "焦段+系列"),
    ("35 1.7", "AF 35mm F1.7 Air", "口语"),
    ("40 Air", "AF 40mm F2.5 Air", "焦段+系列"),
    ("40 2.5", "AF 40mm F2.5 Air", "口语"),
    ("50 Air", "AF 50mm F2.0 Air", "焦段+系列"),
    ("50 F2", "AF 50mm F2.0 Air", "整数光圈"),
    ("56 Air", "AF 56mm F1.7 Air", "焦段+系列"),
    ("56 1.7", "AF 56mm F1.7 Air", "口语"),
    # ── 无系列定焦(目录唯一家族) ───────────────────────────────────────
    ("13 1.4", "AF 13mm F1.4", "口语"),
    ("23 1.4", "AF 23mm F1.4", "口语"),
    ("33 1.4", "AF 33mm F1.4", "口语"),
    ("56 1.4", "AF 56mm F1.4", "口语"),
    ("16 1.8", "AF 16mm F1.8", "口语"),
    ("24 1.8", "AF 24mm F1.8", "口语"),
    ("28 1.8", "AF 28mm F1.8", "口语"),
    ("28 4.5", "AF 28mm F4.5", "口语"),
    ("28mm pancake", "AF 28mm F4.5", "饼干头说法(目录唯一 28mm 饼干)"),
    ("28 饼干", "AF 28mm F4.5", "中文饼干头"),
    ("28 Chip", "AF 28mm F4.5 Chip", "Chip 版"),
    ("85 1.8 II", "AF 85mm F1.8 II", "二代"),
    ("85 1.8 二代", "AF 85mm F1.8 II", "中文二代"),
    ("85mm F1.8 XF", "AF 85mm F1.8 II", "富士口写法(目录 85/1.8 仅 II 代官方行)"),
    ("35 1.8 II", "AF 35mm F1.8 II", "二代"),
    ("MF 20 1.8", "MF 20mm F1.8", "手动 20"),
    # ── 电影头 / EPIC ─────────────────────────────────────────────────────
    ("Epic 50", "EPIC 50mm T2.0 1.33X PL Anamorphic Cine", "EPIC 焦段"),
    ("Epic 35", "EPIC 35mm T2.0 1.33X PL Anamorphic Cine", "EPIC 焦段"),
    ("Epic 75", "EPIC 75mm T2.0 1.33X PL Anamorphic Cine", "EPIC 焦段"),
    ("Epic 100", "EPIC 100mm T2.0 1.33X PL Anamorphic Cine", "EPIC 焦段"),
    ("Epic 135", "EPIC 135mm T2.4 1.33X PL Anamorphic Cine", "EPIC 焦段"),
    ("Epic 65 Macro", "EPIC 65mm T2.8 Macro 1.33X PL Anamorphic Cine", "EPIC 微距"),
    ("Epic 65mm 微距", "EPIC 65mm T2.8 Macro 1.33X PL Anamorphic Cine", "中文微距"),
    # ── 灯光 / 监视器 / 配件 ─────────────────────────────────────────────
    ("Z1 Pro", "Vintage Z1 Pro", "复古闪光灯"),
    ("Z1", "Vintage Z1", "复古闪光灯"),
    ("Z2", "Vintage Z2", "复古闪光灯"),
    ("Z3", "Spark Z3", "Spark 闪光灯"),
    ("Spark Z3", "Spark Z3", "全写"),
    ("K60 灯棒", "K60", "中文灯棒"),
    ("S05 口袋灯", "S05", "中文口袋灯"),
    ("Nexus Focus", "NexusFocus F1", "分写"),
    ("NexusFocus", "NexusFocus F1", "无型号"),
    ("Nexus Focus F1", "NexusFocus F1", "分写+型号"),
    ("NexusFocus F1.0 PL-E", "NexusFocus F1", "全写"),
    ("Nexus 适配器", "NexusFocus F1", "中文适配器"),
    ("TC 2X", "TC-2.0X", "增距镜简写"),
    ("2X teleconverter", "TC-2.0X", "英文增距镜"),
    ("2X 增距镜", "TC-2.0X", "中文增距镜"),
    ("EF-E2", "EF-E II", "罗马数字简写"),
    ("EF-E 二代", "EF-E II", "中文二代"),
    ("DC-X2", "DC-X2", "监视器"),
    ("DC-X3", "DC-X3", "监视器"),
)

# 卡口短语(正则,忽略大小写)→ 规范卡口 token。先改写再截取;只认明确「卡口」语义,
# 不动单独的「索尼」「尼康」(那多半在说机身)。
MOUNT_PHRASES: tuple[tuple[str, str], ...] = (
    (r"(?:索尼|sony)\s*(?:e|fe)?\s*(?:卡口|口|版|mount)", "FE-mount"),
    (r"(?<![a-z0-9])fe\s*(?:卡口|口|版)", "FE-mount"),
    (r"(?<![a-z])e\s*(?:卡口|口)", "FE-mount"),
    (r"\bfor\s+sony\s*(?:e|fe)?(?:[- ]?mount)?\b", "FE-mount"),
    (r"(?:尼康|nikon)\s*z?\s*(?:卡口|口|版|mount)", "Z-mount"),
    (r"(?<![a-z0-9])z\s*(?:卡口|口|版)", "Z-mount"),
    (r"\bfor\s+nikon\s*z?(?:[- ]?mount)?\b", "Z-mount"),
    (r"(?:富士|fuji(?:film)?)\s*(?:x|xf)?\s*(?:卡口|口|版|mount)", "X-mount"),
    (r"(?<![a-z0-9])(?:x|xf)\s*(?:卡口|口|版)", "X-mount"),
    (r"\bfor\s+fuji(?:film)?\s*(?:x|xf)?(?:[- ]?mount)?\b", "X-mount"),
    (r"(?:徕卡|leica|松下|panasonic)\s*l?\s*(?:卡口|口|版|mount)", "L-mount"),
    (r"(?<![a-z0-9])l\s*(?:卡口|口|版)", "L-mount"),
    (r"\bfor\s+(?:leica|panasonic)\s*l?(?:[- ]?mount)?\b", "L-mount"),
    (r"(?:佳能|canon)\s*rf\s*(?:卡口|口|版|mount)", "RF-mount"),
    (r"(?<![a-z0-9])rf\s*(?:卡口|口|版)", "RF-mount"),
    (r"(?:佳能|canon)\s*ef\s*(?:卡口|口|版|mount)", "EF-mount"),
    (r"(?<![a-z0-9])ef\s*(?:卡口|口|版)", "EF-mount"),
    (r"(?<![a-z0-9])pl\s*(?:卡口|口|版)", "PL-mount"),
    (r"(?:m4/3|m43|mft|微单?4/3|奥林巴斯|olympus)\s*(?:卡口|口|版|mount)", "M43"),
)

# 仅系列提及(正则,忽略大小写)→ 系列码;「系列 / series / lineup / 线」可有可无。
SERIES_MARKERS: tuple[tuple[str, str, str], ...] = (
    (r"\bpro\b", "Pro", "Pro 系列"),
    (r"\blab\b", "LAB", "LAB 系列"),
    (r"\bevo\b", "EVO", "EVO 系列"),
    (r"\bair\b", "Air", "Air 系列"),
    (r"\bepics?\b", "EPIC", "EPIC 系列"),
    (r"\bvintage\b", "Vintage", "Vintage 系列"),
)
SERIES_SUFFIX_RE = re.compile(r"\s*(?:系列|series|lineup|line|高端线|产品线|线)(?![a-z])", re.IGNORECASE)

_MOUNT_PHRASE_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), token) for pattern, token in MOUNT_PHRASES
)
_SERIES_MARKER_RES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), code, label) for pattern, code, label in SERIES_MARKERS
)
_APERTURE_TOKEN_RE = re.compile(r"^f(\d(?:\.\d)?)$")
_FOCAL_TOKEN_RE = re.compile(r"^(\d{1,3})mm$")
_AF_GLUED_RE = re.compile(r"^af(\d{1,3}(?:mm)?)$")


def alias_key(value: str) -> str:
    """别名归一键:大小写 / mm / F 前缀 / 斜杠 / 全角 / 中文标点全部压平。

    「85mm F1.4 Pro」「85 1.4 pro」「AF85 f/1.4 Pro」「85/1.4 Pro」→ 同一个键 ``85 1.4 pro``;
    T 光圈保留 t 前缀(电影头与摄影头不互认)。
    """

    text = str(value or "").strip().lower()
    text = text.replace("／", "/").replace("．", ".")
    text = re.sub(r"\bf\s*/\s*(\d)", r"f\1", text)
    text = re.sub(r"\bt\s*/\s*(\d)", r"t\1", text)
    text = re.sub(r"(?<![\d.])(\d{1,3})\s*/\s*(\d(?:\.\d)?)(?![\d])", r"\1 \2", text)
    text = re.sub(r"[^a-z0-9.㐀-鿿]+", " ", text)
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip(".")
        if not token:
            continue
        glued = _AF_GLUED_RE.match(token)
        if glued:
            token = glued.group(1)
        if token == "af":
            continue
        focal = _FOCAL_TOKEN_RE.match(token)
        if focal:
            token = focal.group(1)
        aperture = _APERTURE_TOKEN_RE.match(token)
        if aperture:
            token = aperture.group(1)
        if re.fullmatch(r"\d", token) and tokens and re.fullmatch(r"\d{1,3}", tokens[-1]):
            token = f"{token}.0"
        tokens.append(token)
    return " ".join(tokens)


_ALIAS_INDEX: dict[str, str] = {}
for _alias, _canonical, _note in LENS_ALIASES:
    _ALIAS_INDEX.setdefault(alias_key(_alias), _canonical)


def lookup_lens_alias(value: str) -> str:
    """别名 → canonical(没命中返回空串;调用方再过目录)。"""

    return _ALIAS_INDEX.get(alias_key(value), "")


def rewrite_mount_phrases(text: str) -> str:
    """把中英文卡口短语改写成规范卡口 token(只动卡口语义,别的原样)。"""

    out = str(text or "")
    for pattern, token in _MOUNT_PHRASE_RES:
        out = pattern.sub(f" {token} ", out)
    return re.sub(r"[ \t]{2,}", " ", out)


def series_only_codes(text: str) -> list[str]:
    """整段只剩系列词(可带「系列 / series」尾巴)时返回系列码列表;否则空。"""

    stripped = SERIES_SUFFIX_RE.sub(" ", str(text or ""))
    stripped = re.sub(r"[/,，、&+]+", " ", stripped)
    words = [w for w in re.split(r"\s+", stripped.strip()) if w]
    if not words:
        return []
    codes: list[str] = []
    for word in words:
        code = ""
        for pattern, series_code, _label in _SERIES_MARKER_RES:
            if pattern.fullmatch(word):
                code = series_code
                break
        if not code:
            return []
        if code not in codes:
            codes.append(code)
    return codes


def series_label(code: str) -> str:
    for _pattern, series_code, label in _SERIES_MARKER_RES:
        if series_code == code:
            return label
    return f"{code} 系列"


def alias_rows() -> Iterable[dict[str, str]]:
    """给校验 / 报表用的逐行视图。"""

    for alias, canonical, note in LENS_ALIASES:
        yield {"alias": alias, "alias_key": alias_key(alias), "canonical": canonical, "note": note}


__all__ = [
    "ALIAS_TABLE_VERSION",
    "LENS_ALIASES",
    "MOUNT_PHRASES",
    "SERIES_MARKERS",
    "alias_key",
    "alias_rows",
    "lookup_lens_alias",
    "rewrite_mount_phrases",
    "series_label",
    "series_only_codes",
]
