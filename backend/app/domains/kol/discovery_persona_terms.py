"""KOL 发现 persona 词表与相关度打分(从 discovery_filters.py 抽出,行为不变)。

纯函数+常量:persona 正词分词 / 负词整短语 / 词边界命中判据 / 启发式相关度打分。
零 LLM/零 IO/零 Apify。被 discovery_filters re-export 回灌,既有调用点与 import 路径不变。
拆出原因:discovery_filters.py 因 A1 负词误杀修补而越过 800 行软棘轮,按house规矩抽兄弟文件
(tests/test_line_soft_ratchet.py),不刷快照。
红线:纯过滤/展示信号,零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import re
from typing import Any


# persona 启发式相关度:发现 item 文本 vs 产品 persona 正/负词。泛词不计分,英文优先。
_PERSONA_GENERIC_TERMS = {
    "photo", "photos", "photography", "photographer", "photographers", "video", "videos",
    "videography", "videographer", "content", "creator", "creators", "vlog", "vlogger",
    "vlogging", "camera", "gear", "film", "filmmaker", "filmmaking", "reel", "reels",
    "shoot", "shooting", "and", "the", "for", "with",
    "摄影", "攝影", "摄影师", "攝影師", "视频", "視頻", "创作者", "創作者", "博主", "内容",
    "拍摄", "拍攝", "短视频", "短視頻", "相机", "相機", "器材", "视频创作者",
}


def _persona_entries(*sources: Any) -> list[Any]:
    """归一 persona 字段(list / JSON 串 / dict / None 各自兜底)→ 扁平条目列表。

    从 _persona_term_list 抽出的共用前半段(正词分词路径与负词整短语路径共用),归一
    行为逐字不变;JSON 串解析失败按原串处理(诚实降级,绝不吞成空表)。"""
    out: list[Any] = []
    for src in sources:
        if src is None:
            continue
        value: Any = src
        if isinstance(src, (str, bytes)):
            s = src.decode() if isinstance(src, bytes) else src
            s = s.strip()
            if s[:1] in ("[", "{"):
                try:
                    import json as _json_mod

                    value = _json_mod.loads(s)
                except Exception:
                    value = s
            else:
                value = s
        if isinstance(value, dict):
            value = list(value.values())
        out.extend(value if isinstance(value, (list, tuple, set)) else [value])
    return out


def _persona_term_list(*sources: Any) -> list[str]:
    """归一 persona 字段 → 分词 → 去泛词 → 去重保序(**正词**口径,行为不变)。

    负词绝不再走这里:LLM 的 avoid_types 是人话短语,拆词会连坐(见 _persona_phrase_list)。"""
    out: list[str] = []
    for entry in _persona_entries(*sources):
        for tok in re.split(r"[\s,/、，;；|·\-]+", str(entry or "").lower()):
            tok = tok.strip()
            if len(tok) >= 2 and tok not in _PERSONA_GENERIC_TERMS:
                out.append(tok)
    return list(dict.fromkeys(out))


# ── 负词匹配口径修(A1 误杀,2026-08-25)────────────────────────────────────────────
# 病根实测(prod a05e48dd3,vkpi_kol_pool 2034 人真池当候选流):avoid_types 是 LLM 写的
# 人话短语(如 "cinematographer shooting cinema lenses"),旧口径先按分隔符**拆成单词**、
# 再用**裸子串**匹配、命中即 continue 丢弃 → cinema 连坐 cinematic/cinematography、
# lenses 连坐 lens 系、"still-photography-only photographer" 拆出的 only 连坐一切写了
# only 的 bio,合计 409/2034(20.1%)被静默误杀。
# 修法三条:① 匹配单位 = LLM 原本写的**整短语**(只按真列表分隔符切;空格与连字符是短语
# 内部结构,绝不切);② 匹配走**词边界**(口径抄同仓 profile_recall_match_evidence.
# _contains_term,不另发明);③ persona 负词从「直接丢弃」降级为「排序扣分 + 诚实透出」,
# 真丢弃只留给 _HARD_AVOID_TERMS 静态高精词表(见 _is_hard_avoid)。
_PERSONA_PHRASE_SPLIT_RE = re.compile(r"[,、，;；|\n\r]+")


def _persona_phrase_list(*sources: Any) -> list[str]:
    """归一 persona 字段 → 保留**整短语** → 去泛词 → 去重保序(**负词**口径)。

    只切真列表分隔符(逗号/中文逗号/顿号/分号/竖线/换行);空格与连字符是短语内部结构,
    绝不切 —— "cinematographer shooting cinema lenses" 整条当一个匹配单位,不会再拆出
    cinema 去连坐 cinematic。整条恰好等于泛词(avoid_types=["vlogger"])仍按泛词丢弃,
    不拿泛词当负判据。只有一个单词的项(如 "drone")照样保留,由词边界匹配兜精度。"""
    out: list[str] = []
    for entry in _persona_entries(*sources):
        for phrase in _PERSONA_PHRASE_SPLIT_RE.split(str(entry or "").lower()):
            phrase = " ".join(phrase.split())
            if len(phrase) >= 2 and phrase not in _PERSONA_GENERIC_TERMS:
                out.append(phrase)
    return list(dict.fromkeys(out))


def _term_hit(blob: str, term: str) -> bool:
    """词边界命中判据(口径抄 profile_recall_match_evidence._contains_term,别另发明)。

    含 CJK 的词直接子串(中文无词边界);纯西文词/短语用 (?<![a-z0-9])…(?![a-z0-9]) 前后
    哨兵 —— cinema 不再命中 cinematic/cinematography,lenses 不再命中 lens,only 不再命中
    onlyfans。短语内部空白按 \\s+ 容错(bio 里常是多空格/换行)。
    blob 须已小写(_candidate_blob 的产出即是)。纯函数零 IO。"""
    if not blob or not term:
        return False
    if any("\u4e00" <= ch <= "\u9fff" for ch in term):
        return term in blob
    body = r"\s+".join(re.escape(part) for part in term.split())
    if not body:
        return False
    return re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", blob) is not None


def _persona_positive_terms(product_focus: Any, ideal_creator_types: Any, verticals: Any, query: Any) -> list[str]:
    return _persona_term_list(product_focus, ideal_creator_types, verticals, query)


def _persona_avoid_terms(avoid_types: Any) -> list[str]:
    """persona 负词(avoid_types)→ **整短语**列表(不拆词,见 _persona_phrase_list)。"""
    return _persona_phrase_list(avoid_types)


def _persona_relevance(item: dict[str, Any], *, pos_terms: list[str], neg_terms: list[str]) -> dict[str, Any]:
    """persona 启发式相关度(纯本地零 LLM):扫 item 文本对正/负词命中打分。
    返回 {score, relevance_score, relevance_tier, relevance_hits, persona_avoid_hits};
    score=relevance_score 供落库。
    red line:独立展示信号,绝不并入 viltrox_fit_score / rule_v0。CN/HK/TW 不在此扣分(交 _detect_excluded_region 排)。

    2026-08-25(A1):负词改**整短语 + 词边界**匹配(_term_hit),并把命中短语原样透出到
    persona_avoid_hits —— 负词已从「直接丢弃」降级为这里的扣分,扣分理由必须是可见的,
    不能像旧丢弃那样在结果里静默缺席。正词匹配口径**故意不动**(命中是加分不是杀人,
    失败方向本就安全,不在本刀射程内)。"""
    # 只看候选**自身内容**(标题/频道名/handle/bio);绝不含 search_query —— 那是查询词本身,会自命中致全 1.0。
    # bio 为 K2 富化新增(频道简介/IG biography),真摄影师的自述是最诚实的相关度证据;无 bio 行为不变。
    blob = " ".join(
        str(item.get(k) or "") for k in ("sample_title", "channel_name", "handle", "bio")
    ).lower()
    if not pos_terms and not neg_terms:
        return {
            "score": 0.5, "relevance_score": 0.5, "relevance_tier": "中",
            "relevance_hits": [], "persona_avoid_hits": [],
        }
    hits = [t for t in pos_terms if t in blob]
    neg_hits = [t for t in neg_terms if _term_hit(blob, t)]
    score = 0.35 + 0.18 * len(hits) - 0.30 * len(neg_hits)
    if not hits:
        score = min(score, 0.12)  # 零正命中=泛结果,压低,排序后置
    score = max(0.0, min(1.0, score))
    tier = "高" if score >= 0.6 else ("中" if score >= 0.3 else "低")
    return {
        "score": round(score, 4),
        "relevance_score": round(score, 4),
        "relevance_tier": tier,
        "relevance_hits": hits[:6],
        "persona_avoid_hits": neg_hits[:6],
    }
