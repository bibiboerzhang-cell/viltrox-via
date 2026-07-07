"""CB4 · Dealer 导入脚手架(vkpi_dealers)—— 结构就绪,数据到位即用。

硬件品牌盲区:vkpi_dealers 表已建(迁移 144)但本地 0 行。本脚本提供**幂等**导入结构,
真数据来源到位即可跑,绝不在无源时编数。

数据来源(待接入,注明):
  - B&H / Adorama 门店定位器页(store-locator JSON / 门店列表)
  - 品牌官方 dealer locator(Authorized Dealer 页)
  ↑ 抓取走 Firecrawl(待接入);本脚本只负责「拿到 rows 后幂等落库」。

用法:
    # 干跑(默认):只打印计划,不写库
    PYTHONPATH=backend .venv/bin/python -m scripts.import_dealers_stub --csv path/to/dealers.csv
    # 真写(幂等 UPSERT on (name,address)):
    PYTHONPATH=backend .venv/bin/python -m scripts.import_dealers_stub --csv path/to/dealers.csv --apply

CSV 表头(缺列容错):name,address,city,state,lat,lng,source
幂等:ON CONFLICT (name,address) → COALESCE 补空(镜像 dealer_scrape.upsert 口径)。

铁律:dry_run 默认;不触 viltrox_fit_score / rule_v0;compat SQL 用 ? 占位、无字面 %。
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any


REQUIRED = ("name", "address")
OPTIONAL = ("city", "state", "lat", "lng", "source")

# 无 CSV 时的占位骨架(仅演示结构;真跑请提供 --csv,勿把占位当真数据)。
_PLACEHOLDER_HINT = (
    "未提供 --csv:这是脚手架占位。真数据请从 B&H/Adorama 门店定位器或品牌 dealer "
    "locator(Firecrawl 待接入)导出 CSV 后再跑。"
)


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clean_coord(value: Any, lo: float, hi: float) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _read_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            name = _clean(raw.get("name"))
            address = _clean(raw.get("address"))
            if not name or not address:
                continue  # 幂等键必须齐全,缺则跳过(不编造)
            rows.append(
                {
                    "name": name,
                    "address": address,
                    "city": _clean(raw.get("city")),
                    "state": _clean(raw.get("state")),
                    "lat": _clean_coord(raw.get("lat"), -90, 90),
                    "lng": _clean_coord(raw.get("lng"), -180, 180),
                    "source": _clean(raw.get("source")) or "import_dealers_stub",
                }
            )
    return rows


def _upsert(rows: list[dict[str, Any]]) -> dict[str, int]:
    """幂等 UPSERT into vkpi_dealers(ON CONFLICT (name,address) COALESCE 补空)。"""
    from app.db.connection import close_db_runtime, get_conn, table_exists

    if not table_exists("vkpi_dealers"):
        raise SystemExit("vkpi_dealers 未建表(先应用迁移 144)")

    inserted = 0
    conn = get_conn()
    for r in rows:
        conn.execute(
            """
            INSERT INTO vkpi_dealers (name, address, city, state, lat, lng, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NOW())
            ON CONFLICT (name, address) DO UPDATE SET
                city   = COALESCE(excluded.city,   vkpi_dealers.city),
                state  = COALESCE(excluded.state,  vkpi_dealers.state),
                lat    = COALESCE(excluded.lat,    vkpi_dealers.lat),
                lng    = COALESCE(excluded.lng,    vkpi_dealers.lng),
                source = COALESCE(excluded.source, vkpi_dealers.source)
            """,
            (r["name"], r["address"], r["city"], r["state"], r["lat"], r["lng"], r["source"]),
        )
        inserted += 1
    try:
        conn.commit()
    except Exception:  # noqa: BLE001 — 某些 compat 后端自动提交
        pass
    close_db_runtime()
    return {"processed": inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description="CB4 Dealer 幂等导入脚手架(dry_run 默认)")
    parser.add_argument("--csv", type=str, default="", help="Dealer CSV 路径(name,address[,city,state,lat,lng,source])")
    parser.add_argument("--apply", action="store_true", help="真写库(默认 dry_run 只打印计划)")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 行(0=全部)")
    args = parser.parse_args()

    if not args.csv:
        print("[stub] " + _PLACEHOLDER_HINT)
        print("[stub] rows=0 dry_run=True(无源,不落库)")
        return

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"CSV 不存在:{path}")

    rows = _read_csv(path)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    print(f"[stub] csv={path} parsed_rows={len(rows)} apply={args.apply}")
    if not rows:
        print("[stub] 无有效行(name+address 必须齐全)→ 不落库")
        return

    if not args.apply:
        for r in rows[:5]:
            print(f"[dry_run] would upsert: {r['name']} | {r['address']} | {r.get('state') or '?'}")
        if len(rows) > 5:
            print(f"[dry_run] ... (+{len(rows) - 5} more)")
        print(f"[dry_run] TOTAL would upsert={len(rows)}(加 --apply 真写)")
        return

    result = _upsert(rows)
    print(f"[apply] upserted processed={result['processed']} into vkpi_dealers(幂等 on name,address)")


if __name__ == "__main__":
    main()
