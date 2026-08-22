"""顶栏 Ask「$SKU / 镜头」直达候选(2026-08-22 Ask P1):纯读轻端点。

GET /api/admin/vkpi/catalog/suggest?q=&limit=20
    → {status, q, items:[{sku, display_name, lens_key}], source_status}
    登录 + vkpi:read 即可(员工可读);只回三列,零敏感字段;≤20 行。

领域逻辑在 app.domains.products.catalog_suggest;本文件只做接线。
红线:纯 SELECT;零 LLM;绝不写 viltrox_fit_score / 不触 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.products import catalog_suggest

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-catalog-suggest"])


@router.get("/catalog/suggest")
def catalog_suggest_endpoint(
    q: str = Query(default="", max_length=catalog_suggest.MAX_QUERY_LENGTH),
    limit: int = Query(default=catalog_suggest.LIMIT_DEFAULT, ge=1, le=catalog_suggest.LIMIT_MAX),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    del staff
    from app.db.connection import get_conn, is_postgres_runtime, table_exists

    return catalog_suggest.suggest_catalog(
        get_conn(),
        q,
        limit=limit,
        postgres=is_postgres_runtime(),
        lens_table_exists=table_exists("vkpi_kol_lens_evidence"),
    )
