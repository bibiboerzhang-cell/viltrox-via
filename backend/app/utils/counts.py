"""
utils/counts.py — 数据量级解析（parse_count, metrics 提取）
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

def parse_count(raw: Optional[str]) -> int:
    if not raw:
        return 0
    s = str(raw).lower().replace(",", "").strip()
    m = re.match(r"([0-9]+(?:\.[0-9]+)?)(k|m|w|万)?", s)
    if not m:
        digits = re.sub(r"\D", "", s)
        return int(digits) if digits else 0
    num = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit == "k":
        num *= 1000
    elif unit == "m":
        num *= 1_000_000
    elif unit in {"w", "万"}:
        num *= 10_000
    return round(num)


def try_patterns(text: str, patterns: List[str]) -> Tuple[int, bool]:
    for reg in patterns:
        m = re.search(reg, text, re.I)
        if not m:
            continue
        vals = [g for g in m.groups() if g]
        for v in vals:
            n = parse_count(v)
            if n:
                return n, True
    return 0, False


def parse_metrics_from_text(text: str, platform: str) -> Tuple[Dict[str, int], Dict[str, bool]]:
    t = (text or "").replace("\n", " ")
    patterns = {
        "views": [
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(views|view|播放|浏览|观看)",
            r"(views|view|播放|浏览|观看)\s*[:：]?\s*([0-9.,]+\s?[kKmMwW万]?)",
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(次播放|播放量)",
        ],
        "likes": [
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(次)?\s*(likes|like|点赞|赞|次赞|個讚|次喜歡|upvotes|upvote)",
            r"(likes|like|点赞|赞|次赞|個讚|次喜歡|upvotes|upvote)\s*[:：]?\s*([0-9.,]+\s?[kKmMwW万]?)",
        ],
        "comments": [
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(条)?\s*(comments|comment|評論|评论|replies|reply)",
            r"(comments|comment|評論|评论|replies|reply)\s*[:：]?\s*([0-9.,]+\s?[kKmMwW万]?)",
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(則評論|条评论|comments?)",
        ],
        "shares": [
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(次)?\s*(shares|share|转发|分享|repost)",
            r"(shares|share|转发|分享|repost)\s*[:：]?\s*([0-9.,]+\s?[kKmMwW万]?)",
        ],
        "favorites": [
            r"([0-9.,]+\s?[kKmMwW万]?)\s*(次)?\s*(favorites|favorite|saves|save|收藏|bookmarks?)",
            r"(favorites|favorite|saves|save|收藏|bookmarks?)\s*[:：]?\s*([0-9.,]+\s?[kKmMwW万]?)",
        ],
    }

    metrics: Dict[str, int] = {}
    found: Dict[str, bool] = {}
    for key, regs in patterns.items():
        # YouTube: 分享 is a button label, not a count — skip shares/favorites text matching
        # to prevent "309 分享" being misread as shares=309
        if platform == "YouTube" and key in ("shares", "favorites"):
            metrics[key] = 0
            found[key] = False
            continue
        metrics[key], found[key] = try_patterns(t, regs)

    if platform == "Instagram" and not found["views"]:
        metrics["views"] = 0

    return metrics, found


def parse_metrics_from_html(html: str, platform: str) -> Tuple[Dict[str, int], Dict[str, bool]]:
    metrics = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0}
    found = {k: False for k in metrics}
    if not html:
        return metrics, found

    pairs: Dict[str, List[str]] = {}

    if platform == "TikTok":
        pairs = {
            "views": [r'"playCount":\s*(\d+)', r'"viewCount":\s*(\d+)'],
            "likes": [r'"diggCount":\s*(\d+)', r'"likeCount":\s*(\d+)'],
            "comments": [r'"commentCount":\s*(\d+)'],
            "shares": [r'"shareCount":\s*(\d+)'],
            "favorites": [r'"collectCount":\s*(\d+)', r'"favoriteCount":\s*(\d+)'],
        }
    elif platform == "Instagram":
        pairs = {
            "likes": [r'"edge_media_preview_like":\{"count":(\d+)', r'"like_count":\s*(\d+)'],
            "comments": [r'"edge_media_to_parent_comment":\{"count":(\d+)', r'"comment_count":\s*(\d+)'],
            "views": [r'"video_view_count":\s*(\d+)', r'"play_count":\s*(\d+)'],
        }
    elif platform == "YouTube":
        pairs = {
            "views": [r'"viewCount":"(\d+)"', r'"shortViewCount":\{"simpleText":"([0-9,\.KM万w]+)'],
            "likes": [
                r'"label":"([0-9,\.KM万w]+)\s+likes"',
                r'"defaultText":\{"accessibility":\{"accessibilityData":\{"label":"([0-9,\.KM万w]+)\s+likes"',
                # Chinese YouTube interface: 点赞 button shows count
                r'"toggleButtonRenderer".*?"label":"([0-9,\.KM万w]+)\s*(?:likes|个赞|次点赞)"',
                r'([0-9,\.KM万w]+)\s*(?:个赞|次点赞|likes)',
                # Like count in accessibility data
                r'"accessibilityData":\{"label":"([0-9,\.KM万w]+)\s+likes"',
            ],
            "comments": [
                r'"countText":\{"runs":\[\{"text":"([0-9,\.KM万w]+)"\}',
                r'([0-9,\.KM万w]+)\s*(則評論|条评论|comments?)',
            ],
            # YouTube does NOT publicly show share counts — always 0
            # Do NOT add shares patterns here to avoid misreading 分享 button label
        }
    elif platform == "Facebook":
        pairs = {
            "likes": [
                r'"reaction_count":\{"count":(\d+)',
                r'"like_count":(\d+)',
                r'"reactionCount":\{"total_count":(\d+)',
                r'([0-9,\.KM]+)\s*(?:Likes?|Reactions?)',
            ],
            "comments": [
                r'"comment_count":(\d+)',
                r'"total_comment_count":(\d+)',
                r'([0-9,\.KM]+)\s*Comments?',
            ],
            "shares": [
                r'"share_count":\{"count":(\d+)',
                r'([0-9,\.KM]+)\s*Shares?',
            ],
            "views": [
                r'"video_view_count":(\d+)',
                r'"view_count":(\d+)',
                r'([0-9,\.KM]+)\s*(?:Views?|Plays?)',
            ],
        }
    elif platform == "Reddit":
        pairs = {
            "likes": [r'"score":\s*(\d+)', r'"ups":\s*(\d+)'],
            "comments": [r'"num_comments":\s*(\d+)'],
        }

    for key, regs in pairs.items():
        val, ok = try_patterns(html, regs)
        metrics[key] = val
        found[key] = ok
    return metrics, found


def merge_metrics(
    primary: Dict[str, int], primary_found: Dict[str, bool],
    fallback: Dict[str, int], fallback_found: Dict[str, bool]
) -> Tuple[Dict[str, int], Dict[str, bool]]:
    out: Dict[str, int] = {}
    found: Dict[str, bool] = {}
    for key in primary.keys():
        if primary_found.get(key):
            out[key] = primary[key]
            found[key] = True
        elif fallback_found.get(key):
            out[key] = fallback[key]
            found[key] = True
        else:
            out[key] = 0
            found[key] = False
    return out, found

