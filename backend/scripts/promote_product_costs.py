"""Promote legacy product costs (staging) into vkpi_product_cost_catalog.

来源:vkpi_legacy_product_costs_staging(2026-05-19 飞书『产品成本信息表』导入,
834 行全 CNY,import_action='stage_only' 从未晋升)→ 正式表 vkpi_product_cost_catalog
(批4 产品成本估算的数据源,此前 0 行,导致前端估算恒 0、项目产品 datalist 恒空)。

规则:
- 仅 review_status='ready' 行;needs_review 单列清单打印,人工裁后再说。
- 同一 staging sku 多行取 updated_at 最新一行。
- 规范 SKU 匹配:normalize_alias(sku/product_name) → vkpi_product_aliases.alias_norm,
  唯一命中用规范 SKU;零命中/多义保留原始型号原文(配件/支架等不在官网镜头目录,
  属预期;前端 productUnitCosts 按 sku 与名称双键查找,原文同样可命中)。
- 币种:CNY → USD,固定汇率 RATE=7.20(估算口径;费用 UI 本就标『估』徽章)。
  note 保留 ¥原值 + 汇率 + 原始型号,staging 行永不删除,溯源完整。
- 幂等:product_sku 已在 catalog 则跳过(重跑 no-op)。

用法:
  python scripts/promote_product_costs.py          # 干跑,只打印
  python scripts/promote_product_costs.py --apply  # 实际写入
"""
from __future__ import annotations

import sys

from app.db.connection import db_connection_sync_scope, get_conn
from app.domains.products.product_aliases import normalize_alias

RATE_CNY_USD = 7.20
CREATED_BY_STAFF_ID = 84  # Jianbo(user_id=108 的真 staff.id)


def main(apply: bool) -> None:
    conn = get_conn()
    aliases: dict[str, set[str]] = {}
    for row in conn.execute("SELECT alias_norm, sku FROM vkpi_product_aliases").fetchall():
        aliases.setdefault(row["alias_norm"], set()).add(row["sku"])

    existing = {
        row["product_sku"]
        for row in conn.execute("SELECT product_sku FROM vkpi_product_cost_catalog").fetchall()
    }

    needs_review = conn.execute(
        "SELECT sku, product_name, cost FROM vkpi_legacy_product_costs_staging WHERE review_status<>'ready' ORDER BY sku",
    ).fetchall()

    # 每 sku 取最新一行(ready)
    rows = conn.execute(
        """
        SELECT DISTINCT ON (sku) sku, product_name, cost, currency, row_uid, import_batch_id
        FROM vkpi_legacy_product_costs_staging
        WHERE review_status='ready'
        ORDER BY sku, updated_at DESC, id DESC
        """,
    ).fetchall()

    promoted_canonical = promoted_original = skipped_existing = skipped_invalid = 0
    for row in rows:
        raw_sku = str(row["sku"] or "").strip()
        name = str(row["product_name"] or "").strip() or raw_sku
        try:
            cost_cny = float(row["cost"] or 0)
        except (TypeError, ValueError):
            cost_cny = 0.0
        if not raw_sku or cost_cny <= 0:
            skipped_invalid += 1
            continue

        candidates: set[str] = set()
        for value in (raw_sku, name):
            norm = normalize_alias(value)
            if norm in aliases:
                candidates |= aliases[norm]
        if len(candidates) == 1:
            target_sku = next(iter(candidates))
            canonical = True
        else:
            target_sku = raw_sku  # 原文保留(配件等不在官网目录,或多义不猜)
            canonical = False

        if target_sku in existing:
            skipped_existing += 1
            continue
        existing.add(target_sku)

        unit_cost_cents = round(cost_cny / RATE_CNY_USD * 100)
        note = (
            f"晋升自 legacy staging:¥{cost_cny:g} CNY @ {RATE_CNY_USD}"
            f";原型号:{raw_sku};batch={row['import_batch_id']};row_uid={row['row_uid']}"
        )
        if apply:
            conn.execute(
                """
                INSERT INTO vkpi_product_cost_catalog
                  (product_sku, product_name, unit_cost_cents, currency, active, note,
                   created_by_staff_id, updated_by_staff_id, created_at, updated_at)
                VALUES (?, ?, ?, 'USD', TRUE, ?, ?, ?, NOW(), NOW())
                """,
                (target_sku, name, unit_cost_cents, note, CREATED_BY_STAFF_ID, CREATED_BY_STAFF_ID),
            )
        if canonical:
            promoted_canonical += 1
        else:
            promoted_original += 1

    if apply:
        conn.commit()  # scope 退出时 close() 会 rollback 未提交事务(connection.py:358),必须显式提交
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] staging ready 去重 {len(rows)} 行:")
    print(f"  晋升·规范SKU匹配 = {promoted_canonical}")
    print(f"  晋升·原始型号原文 = {promoted_original}")
    print(f"  跳过·catalog已有 = {skipped_existing}")
    print(f"  跳过·无效(空sku/成本<=0) = {skipped_invalid}")
    print(f"\nneeds_review {len(needs_review)} 行(未晋升,待人工裁):")
    for row in needs_review:
        print(f"  - {row['sku']} | {row['product_name']} | cost={row['cost']}")


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    with db_connection_sync_scope():
        main(apply_flag)
