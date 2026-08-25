"""把 raw_platform_data 里现成的国家码投影到查询列(2026-08-25)。

背景:硬筛 `_candidate_filter_verdict` 对 country/language 是「未知即拒」,而全池
995/2034 行(48.9%)的 country 投影列是空的 —— 其中 395 行的 raw_platform_data
里**本来就抓到了国家**,只是从没被投影出来。实测可回填 282 行(标准两字母码),
其中 84 行粉丝 ≥5 万。这批人不是「不是美国人」,是「我们没把已有的值搬过来」。

设计取向:
- **默认干跑**,必须 --apply 才写;
- **只填空值**,SQL 侧 `WHERE country IS NULL OR country=''` 兜底,并发写端永远赢;
- **解析保守**:只认 ISO 3166-1 alpha-2 形态(两个字母)。像 "Unknown"、
  "Worldwide" 这类长值一律跳过并计数,宁可不填也不猜;
- **不动 language**:实测 raw 里根本没有 language 键(0 行),没有可回填的东西,
  假装能填只会制造假数据;
- **不接进任何自动流程**,由人手动跑并核对分布。

跑法(产线,先干跑):
    ssh viltrox 'cd /tmp && set -a && source /opt/viltrox-2.0/.env >/dev/null 2>&1 \\
      && set +a && ENVIRONMENT=production APP_ROLE=admin-web ENABLE_SCHEDULER=0 \\
      PYTHONDONTWRITEBYTECODE=1 /opt/viltrox-2.0/current/.venv/bin/python \\
      /opt/viltrox-2.0/current/scripts/ops/backfill_country_language.py'
确认分布无误后再加 --apply。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(1, str(_SCRIPTS_DIR))

from stdout_utils import out as stdout_out  # noqa: E402

# 只认标准两字母国家码;三字母、全称、"Unknown" 一律不猜。
_ALPHA2_LENGTH = 2
# raw 里出现过的键名变体(实测:country 577 次、countryCode 227 次)。
_COUNTRY_KEY_MARKER = "country"
_MAX_WALK_DEPTH = 6
_MAX_LIST_SCAN = 40


def _country_candidates(node: Any, out: list[tuple[str, str]], depth: int = 0) -> None:
    """深度遍历 dict 与 list,收集所有键名含 country 的字符串值。

    必须走 list:实测 227 处 countryCode 藏在嵌套结构里,只遍历 dict 会漏掉七成。
    """
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if _COUNTRY_KEY_MARKER in str(key).lower() and isinstance(value, str) and value.strip():
                out.append((str(key), value.strip()))
            _country_candidates(value, out, depth + 1)
    elif isinstance(node, list):
        for value in node[:_MAX_LIST_SCAN]:
            _country_candidates(value, out, depth + 1)


def resolve_country_code(raw_text: Any) -> tuple[str, str]:
    """从一行 raw 里解出可信的国家码。返回 (码, 判定理由);解不出返回 ("", 理由)。"""
    if not raw_text:
        return "", "raw_empty"
    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError):
        return "", "raw_not_json"
    found: list[tuple[str, str]] = []
    _country_candidates(parsed, found)
    if not found:
        return "", "no_country_key"
    for key, value in found:
        if len(value) == _ALPHA2_LENGTH and value.isalpha():
            return value.upper(), f"key:{key}"
    # 有键但值不是标准码(实测多为 7 字符长值)——不猜,交人处理。
    return "", "non_alpha2_value"


def _rows_to_backfill(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, followers, raw_platform_data::text AS raw FROM vkpi_kol_pool "
        "WHERE (country IS NULL OR country = ?) "
        "AND strpos(LOWER(COALESCE(raw_platform_data::text, ?)), ?) > 0 "
        "ORDER BY id",
        ("", "", _COUNTRY_KEY_MARKER),
    ).fetchall()
    return [dict(row) for row in rows]


def build_plan(conn: Any) -> dict[str, Any]:
    """纯读:算出将要写什么。写端与干跑共用这一个函数,口径不可能漂。"""
    plan: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    high_reach = 0
    for row in _rows_to_backfill(conn):
        code, reason = resolve_country_code(row.get("raw"))
        if not code:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        followers = int(row.get("followers") or 0)
        if followers >= 50_000:
            high_reach += 1
        plan.append({"id": int(row["id"]), "country": code, "reason": reason, "followers": followers})
    by_code: dict[str, int] = {}
    for item in plan:
        by_code[item["country"]] = by_code.get(item["country"], 0) + 1
    return {
        "scanned": len(_rows_to_backfill(conn)),
        "writable": len(plan),
        "high_reach_writable": high_reach,
        "skipped_by_reason": skipped,
        "distinct_codes": len(by_code),
        "top_code_count": max(by_code.values()) if by_code else 0,
        "plan": plan,
    }


def apply_plan(conn: Any, plan: list[dict[str, Any]]) -> int:
    """逐行写入。SQL 侧再兜一次「只填空值」,防止干跑与写入之间有并发补上。"""
    written = 0
    for item in plan:
        cursor = conn.execute(
            "UPDATE vkpi_kol_pool SET country=? WHERE id=? AND (country IS NULL OR country=?)",
            (item["country"], item["id"], ""),
        )
        written += int(getattr(cursor, "rowcount", 0) or 0)
    conn.commit()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 raw 里现成的国家码投影到查询列(默认干跑)")
    parser.add_argument("--apply", action="store_true", help="真正写入;不加则只打印将写什么")
    args = parser.parse_args(argv)

    from app.db.connection import get_conn

    conn = get_conn()
    result = build_plan(conn)
    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "scanned": result["scanned"],
        "writable": result["writable"],
        "high_reach_writable": result["high_reach_writable"],
        "skipped_by_reason": result["skipped_by_reason"],
        "distinct_codes": result["distinct_codes"],
    }
    if not args.apply:
        summary["written"] = 0
        summary["note"] = "干跑:一行未写。核对 writable 与预期(实测 282)吻合后再加 --apply。"
        stdout_out(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    summary["written"] = apply_plan(conn, result["plan"])
    summary["note"] = "已写入;language 列不在本脚本范围(raw 里无可回填值)。"
    stdout_out(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover - 运维入口
    sys.exit(main())
