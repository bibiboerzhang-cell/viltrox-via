"""Best-effort comment language detection with bounded operational logging.

两层接口:
* ``detect_comment_language(text, logger=)``:采集写入路径的既有契约(langdetect.detect 优先,
  退 audience_language 启发式);本波只在最前面加了「非拉丁字系直判」短路,拉丁文本行为不变。
* ``language_detect(text)``:保守版(优化波 B · D 车道),给回填脚本 / 统计口径用:
  短文本、纯 emoji、混合字系一律返回 None(诚实未知),拉丁文本要求 langdetect 置信度 >= 0.85
  且语种在主流集合(或文本够长);冷门语种(so/af/cy/et/sw 这类短文本误判常客)需更长文本才认。
零 LLM、零网络;红线不触 viltrox_fit_score。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# ── 字系 → 语言(非拉丁字系一眼可判;CJK 汉字要配合假名/谚文再细分)──
_SCRIPT_LANG: tuple[tuple[str, int, int], ...] = (
    ("ja", 0x3040, 0x30FF),   # 平假名 + 片假名
    ("ko", 0xAC00, 0xD7AF),   # 谚文音节
    ("ko", 0x1100, 0x11FF),
    ("ko", 0x3130, 0x318F),
    ("zh", 0x4E00, 0x9FFF),   # CJK 统一汉字(无假名时判 zh)
    ("zh", 0x3400, 0x4DBF),
    ("ru", 0x0400, 0x04FF),   # 西里尔(粗口径 ru;乌克兰/保加利亚等同字系不细分)
    ("ar", 0x0600, 0x06FF),
    ("ar", 0x0750, 0x077F),
    ("he", 0x0590, 0x05FF),
    ("th", 0x0E00, 0x0E7F),
    ("hi", 0x0900, 0x097F),   # 天城文(粗口径 hi)
    ("bn", 0x0980, 0x09FF),
    ("el", 0x0370, 0x03FF),
    ("ka", 0x10A0, 0x10FF),
    ("hy", 0x0530, 0x058F),
    ("ta", 0x0B80, 0x0BFF),
    ("my", 0x1000, 0x109F),
    ("km", 0x1780, 0x17FF),
)
_LATIN_RANGES = ((0x0041, 0x024F), (0x1E00, 0x1EFF))
# 拉丁字系里 langdetect 短文本就能稳判的主流语种;其余语种需 >= LONG_TEXT_WORDS 个词才认。
_MAJOR_LATIN = frozenset(
    {"en", "es", "pt", "de", "fr", "it", "id", "tr", "nl", "pl", "vi", "ms", "ro", "sv",
     "da", "no", "fi", "cs", "hu", "tl"}
)
MIN_LETTERS = 6          # 拉丁文本至少 6 个字母才送检
MIN_WORDS = 2            # 且至少 2 个词(单词 / 纯名字不判)
LONG_TEXT_WORDS = 8      # 冷门语种需要的最短词数
MIN_SCRIPT_CHARS = 2     # 非拉丁字系至少 2 个字符
SCRIPT_DOMINANCE = 0.6   # 主字系占字母数 >= 60% 才算「单一语言」,否则混合 -> None
LANGDETECT_MIN_PROB = 0.85
LANGDETECT_SURE_PROB = 0.99  # 短文本无停用词互证时 langdetect 需几乎确定
MIXED_SCRIPT_SHARE = 0.2     # 任一非拉丁字系占比超过此值 -> 混合语,不判

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"[@#][\w.]+")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_REPEAT_RE = re.compile(r"(.)\1{2,}")


def _script_of(char: str) -> str:
    code = ord(char)
    for lo, hi in _LATIN_RANGES:
        if lo <= code <= hi:
            return "latin"
    for lang, lo, hi in _SCRIPT_LANG:
        if lo <= code <= hi:
            return lang
    return ""


def strip_noise(text: str) -> str:
    """去 URL / @提及 / #话题 / emoji 与符号,只留字母、数字、空白与基本标点。"""
    cleaned = _URL_RE.sub(" ", str(text or ""))
    cleaned = _MENTION_RE.sub(" ", cleaned)
    out: list[str] = []
    for char in cleaned:
        category = unicodedata.category(char)
        if category.startswith(("L", "M", "N", "Z")) or char in "'’-.,!?":
            out.append(char)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def script_profile(text: str) -> dict[str, Any]:
    """字母按字系计数;返回 {counts, total, dominant, share}。"""
    counts: dict[str, int] = {}
    total = 0
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue
        script = _script_of(char)
        if not script:
            continue
        total += 1
        counts[script] = counts.get(script, 0) + 1
    if not total:
        return {"counts": {}, "total": 0, "dominant": "", "share": 0.0}
    dominant = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return {"counts": counts, "total": total, "dominant": dominant, "share": counts[dominant] / total}


def script_language(text: str) -> str | None:
    """非拉丁字系直判(短路 langdetect):主字系 >= 60% 且字符数够才给结论;汉字+假名 -> ja。"""
    profile = script_profile(text)
    if not profile["total"]:
        return None
    dominant = str(profile["dominant"])
    if dominant == "latin" or profile["share"] < SCRIPT_DOMINANCE:
        return None
    counts = profile["counts"]
    if dominant == "zh" and counts.get("ja"):
        return "ja"
    if counts.get(dominant, 0) < MIN_SCRIPT_CHARS:
        return None
    return dominant


def _langdetect_guess(text: str) -> tuple[str, float]:
    try:
        from langdetect import DetectorFactory, detect_langs  # type: ignore
    except Exception:
        return "", 0.0
    try:
        DetectorFactory.seed = 0
        ranked = detect_langs(text)
    except Exception:
        return "", 0.0
    if not ranked:
        return "", 0.0
    top = ranked[0]
    return str(getattr(top, "lang", "") or "").lower()[:10], float(getattr(top, "prob", 0.0) or 0.0)


def _heuristic_guess(words: list[str]) -> tuple[str, int]:
    """停用词启发式(audience_language 词表):返回 (语言, 命中停用词数);无命中 ("", 0)。"""
    try:
        from app.domains.kol import audience_language
    except Exception:
        return "", 0
    lowered = {w.lower() for w in words}
    best, best_hits = "", 0
    for lang, stopwords in audience_language._STOPWORDS.items():
        hits = len(lowered & stopwords)
        if hits > best_hits:
            best, best_hits = lang, hits
    return best, best_hits


def language_detect(text: str) -> str | None:
    """保守版单条语言判定;判不准一律 None(诚实未知),绝不拿短文本/emoji/混合语硬猜。

    口径:非拉丁字系主导 -> 字系直判;任一非拉丁字系占比 > MIXED_SCRIPT_SHARE -> 混合语 None;
    拉丁文本 < MIN_WORDS 词或 < MIN_LETTERS 字母 -> None;短文本(< LONG_TEXT_WORDS 词)要求
    langdetect 与停用词启发式互证(或停用词命中 >= 2,或 langdetect >= 0.99 且主流语种);
    长文本 langdetect >= LANGDETECT_MIN_PROB 即认。
    """
    cleaned = strip_noise(text)
    if not cleaned:
        return None
    direct = script_language(cleaned)
    if direct:
        return direct
    profile = script_profile(cleaned)
    if not profile["total"] or profile["dominant"] != "latin":
        return None
    counts = profile["counts"]
    if any(n / profile["total"] > MIXED_SCRIPT_SHARE for script, n in counts.items() if script != "latin"):
        return None
    latin_only = "".join(ch if _script_of(ch) == "latin" or not unicodedata.category(ch).startswith("L") else " " for ch in cleaned)
    latin_only = " ".join(latin_only.split())
    words = [w for w in _WORD_RE.findall(latin_only) if len(w) > 1]
    if int(counts.get("latin", 0)) < MIN_LETTERS or len(words) < MIN_WORDS:
        return None
    if all(_REPEAT_RE.search(w) for w in words):
        return None  # "lolll hahaha" 这类全是拉长词的感叹
    code, prob = _langdetect_guess(latin_only)
    heuristic, hits = _heuristic_guess(words)
    if len(words) >= LONG_TEXT_WORDS:
        if code and prob >= LANGDETECT_MIN_PROB:
            return code
        return heuristic if hits >= 2 else None
    if code and prob >= LANGDETECT_MIN_PROB and code == heuristic:
        return code
    if hits >= 2:
        return heuristic
    if code and prob >= LANGDETECT_SURE_PROB and code in _MAJOR_LATIN and hits == 0 and not heuristic:
        return code
    return None


def detect_comment_language(text: str, *, logger: Any) -> str | None:
    """Prefer langdetect, then fall back to the deterministic local heuristic."""
    normalized = str(text or "").strip()
    if len(normalized) < 3:
        return None
    # 非拉丁字系直判:汉字/假名/谚文/西里尔/阿拉伯等 langdetect 短文本常误判(zh-cn/ko 互串),
    # 字系本身就是确定答案;拉丁文本保持既有 langdetect 路径不变。
    try:
        direct = script_language(strip_noise(normalized))
    except Exception:
        direct = None
    if direct:
        return direct
    try:
        from langdetect import DetectorFactory, detect  # type: ignore
        from langdetect.lang_detect_exception import ErrorCode, LangDetectException  # type: ignore
    except ModuleNotFoundError as exc:
        # The package is optional. Missing transitive dependencies are not.
        if exc.name != "langdetect":
            logger.warning("langdetect 依赖导入异常,退下一级语言检测(best-effort)", exc_info=True)
    except ImportError:
        logger.warning("langdetect 接口导入异常,退下一级语言检测(best-effort)", exc_info=True)
    else:
        try:
            DetectorFactory.seed = 0
            code = str(detect(normalized) or "").strip().lower()
            if code and code != "unknown":
                return code[:10]
        except LangDetectException as exc:
            if exc.get_code() == ErrorCode.CantDetectError:
                logger.debug("langdetect 无足够特征,退下一级语言检测(best-effort)")
            else:
                logger.warning(
                    "langdetect 内部状态异常(code=%s),退下一级语言检测(best-effort)",
                    exc.get_code(),
                    exc_info=True,
                )
        except Exception:
            logger.warning("langdetect 非预期异常,退下一级语言检测(best-effort)", exc_info=True)
    try:
        from app.domains.kol.audience_language import detect_lang

        code = detect_lang(normalized)
        return code if code and code != "und" else None
    except Exception:
        logger.warning("评论语言启发式回退异常,返回未知语言", exc_info=True)
        return None
