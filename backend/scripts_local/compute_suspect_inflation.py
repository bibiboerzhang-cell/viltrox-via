"""P0-3 假粉/异常号规则离群 离线批跑(2026-06-13)。

纯现有数据三规则(高粉低播 / ER 对同量级 peer z-score 离群 / real_er 与生产 ER 背离)。
只写 suspect_inflation* 五根独立列;rule_v0 / viltrox_fit_score 全程零触。
默认 dry-run(打印候选),--execute 才落库——镜像 compute_real_er.py 约定。

用法(须用 .venv,MEMORY:裸 python3 缺 boto3):
    cd backend && ../.venv/bin/python scripts_local/compute_suspect_inflation.py            # dry-run
    cd backend && ../.venv/bin/python scripts_local/compute_suspect_inflation.py --execute   # 落库
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.connection import db_connection_sync_scope  # noqa: E402
from app.domains.kol import pool as kol_pool  # noqa: E402

EXECUTE = "--execute" in sys.argv


def main() -> None:
    with db_connection_sync_scope():
        result = kol_pool.detect_inflation(execute=EXECUTE)
        for item in result.get("items", []):
            if item.get("suspect"):
                print(f"FLAG kol={item['id']} :: {item['reason']}")
        mode = "EXECUTE" if result.get("executed") else "DRY-RUN"
        print(f"[{mode}] scanned={result['scanned']} flagged={result['flagged']} method={result['method']}")
        if not EXECUTE:
            print("加 --execute 落库(只写 suspect_inflation* 独立列,零触 viltrox_fit_score)")


if __name__ == "__main__":
    main()
