"""KOL 语言推断:从「个人简介 + 视频标题」估算创作者语言(推断值,绝不冒充自报值)。

背景:池里只有约三成的人有平台自报语言字段,直接按「语言为空」硬筛会把七成人误杀。
本模块把创作者自己写下的文本(简介、视频标题/文案)当作语言证据,推断出一个**单独存放**
的语言值,与自报值分列两处、来源可追。

口径与红线:
* **复用**评论域的保守检测器 ``app.domains.comments.language_detection.language_detect``,
  不另造一套判定、不引新依赖、不调低任何既有阈值(MIN_LETTERS / MIN_WORDS /
  LANGDETECT_MIN_PROB 等一律沿用)。检测器判不准就是 None。
* 本模块只在「多条文本如何合并成一个人的结论」这一层加规则,且规则只会**更严**:
  逐条判定 + 投票,主语言占比不足 ``MIN_AGREEMENT_SHARE`` 视为混合 -> 未知。
* 判不出的人是「未知」,不是「不合格」——与新鲜闸拆桶口径一致。
* 纯文本推理,零 LLM、零网络、零 DB;不触碰 viltrox_fit_score 及任何质量阈值。

合并策略为什么选投票而不是拼接(``STRATEGY_JOIN`` 仅供离线对照,不作生产口径):
拼接会把十条各自「证据不足」的短标题拼成一段长文本,从而绕过检测器的
``LONG_TEXT_WORDS`` 长文本阈值——那等于变相放宽阈值,且一个中英混发的创作者会被拼成
一段偏向某一侧的长文本而得到过度自信的结论。逐条判定 + 投票则让每条文本各自过原阈值,
再要求多数一致,不一致就诚实回到未知。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Sequence

from app.domains.comments.language_detection import language_detect

# 推断器版本号:写进落库的 language_inferred_method,口径变了要升版本。
KOL_LANGUAGE_INFERENCE_VERSION = "kol_content_langdetect_vote_v1"

STRATEGY_VOTE = "vote"
STRATEGY_JOIN = "join"

MAX_TEXT_SAMPLES = 30        # 每人最多取多少条文本参与投票(够稳且不炸 CPU)
MIN_TEXT_CHARS = 2           # 低于此长度的文本连送检都不必
MIN_AGREEMENT_SHARE = 0.6    # 主语言票数占比 < 60% -> 混合,判未知
HIGH_CONFIDENCE_VOTES = 3    # 高置信:>= 3 票一致
HIGH_CONFIDENCE_SHARE = 0.8  # 且一致率 >= 80%

SOURCE_BIO = "bio"
SOURCE_TITLES = "video_titles"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

REASON_NO_TEXT = "no_text"          # 完全没有可推断的文本
REASON_NO_VERDICT = "no_verdict"    # 有文本,但每条都判不准(太短/纯 emoji/纯 URL)
REASON_MIXED = "mixed"              # 有结论但语言之间无多数,视为混合语


def _clean_texts(values: Iterable[Any] | None) -> list[str]:
    """去空、去重(保序)、截样本上限;不改写文本内容。"""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = str(value or "").strip()
        if len(text) < MIN_TEXT_CHARS:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_TEXT_SAMPLES:
            break
    return out


def collect_language_texts(
    *,
    bio: Any = None,
    titles: Sequence[Any] | None = None,
) -> list[tuple[str, str]]:
    """把一个人的可用文本归一成 [(来源, 文本)];来源只有 bio / video_titles 两类。"""
    items: list[tuple[str, str]] = []
    for text in _clean_texts([bio]):
        items.append((SOURCE_BIO, text))
    for text in _clean_texts(titles):
        items.append((SOURCE_TITLES, text))
    return items


def _unknown(reason: str, sample_n: int, decided_n: int = 0, votes: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "language": None,
        "confidence": None,
        "source": "",
        "sample_n": int(sample_n),
        "decided_n": int(decided_n),
        "votes": dict(votes or {}),
        "unknown_reason": reason,
        "method": KOL_LANGUAGE_INFERENCE_VERSION,
    }


def _confidence(top_votes: int, share: float) -> str:
    if top_votes >= HIGH_CONFIDENCE_VOTES and share >= HIGH_CONFIDENCE_SHARE:
        return CONFIDENCE_HIGH
    if top_votes >= 2:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _source_label(sources: Iterable[str]) -> str:
    ordered = [name for name in (SOURCE_BIO, SOURCE_TITLES) if name in set(sources)]
    return "+".join(ordered)


def _infer_by_vote(items: Sequence[tuple[str, str]]) -> dict[str, Any]:
    verdicts: list[tuple[str, str]] = []
    for source, text in items:
        code = language_detect(text)
        if code:
            verdicts.append((source, str(code)))
    if not verdicts:
        return _unknown(REASON_NO_VERDICT, len(items))
    votes = Counter(code for _source, code in verdicts)
    top_code, top_votes = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    share = top_votes / len(verdicts)
    if share < MIN_AGREEMENT_SHARE:
        return _unknown(REASON_MIXED, len(items), len(verdicts), dict(votes))
    contributing = {source for source, code in verdicts if code == top_code}
    return {
        "language": top_code,
        "confidence": _confidence(top_votes, share),
        "source": _source_label(contributing),
        "sample_n": len(items),
        "decided_n": len(verdicts),
        "votes": dict(votes),
        "unknown_reason": "",
        "method": KOL_LANGUAGE_INFERENCE_VERSION,
    }


def _infer_by_join(items: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """离线对照口径:所有文本拼成一段再判一次。**不作生产口径**(见模块 docstring)。"""
    blob = " . ".join(text for _source, text in items)
    code = language_detect(blob)
    if not code:
        return _unknown(REASON_NO_VERDICT, len(items))
    return {
        "language": str(code),
        "confidence": CONFIDENCE_LOW,
        "source": _source_label(source for source, _text in items),
        "sample_n": len(items),
        "decided_n": 1,
        "votes": {str(code): 1},
        "unknown_reason": "",
        "method": KOL_LANGUAGE_INFERENCE_VERSION + "+join",
    }


def infer_language_from_content(
    *,
    bio: Any = None,
    titles: Sequence[Any] | None = None,
    strategy: str = STRATEGY_VOTE,
) -> dict[str, Any]:
    """从简介 + 视频标题推断语言;判不准返回 language=None(诚实未知)。

    返回:``{language, confidence, source, sample_n, decided_n, votes, unknown_reason, method}``。
    ``language`` 为 None 时 ``unknown_reason`` 说明为什么判不出,给门面如实展示。
    """
    items = collect_language_texts(bio=bio, titles=titles)
    if not items:
        return _unknown(REASON_NO_TEXT, 0)
    if strategy == STRATEGY_JOIN:
        return _infer_by_join(items)
    return _infer_by_vote(items)
