"""CB4 · Dealer(硬件零售商)geo/category 适配评分 v0 —— Channel Brain 层。

背景:硬件品牌盲区。vkpi_dealers 表已建(迁移 144)但本地 0 行 —— 本模块只做
「评分逻辑 + 导入就绪」,数据到位即用,绝不在缺数据时编造。

真列侦察(2026-07-07,vkpi_dealers 迁移 144):
  id / name / address / city / state / lat / lng / source / created_at
  —— 只有地理列,**没有** category / specialty / partner_tier / brand 列。
  因此 v0 的 category_fit / tier_fit 对「真库 Dealer」恒为「未知→中性」并如实标注;
  只有当调用方(fixture 或未来加列后的真行)在 dealer dict 里带 categories / specialty /
  tier 字段时,这两个分量才真正参与判别(ready_when:给 vkpi_dealers 加 specialty/tier 列)。

评分口径(v0,纯启发式先验,绝非真实销售潜力):
  dealer_fit_score(0..100) = 100 × (
      0.50 × geo_fit        # 门店所在州的美国相机零售市场密度先验(公开常识,非真实 sell-through)
    + 0.35 × category_fit   # 门店品类/专长与产品品类的重合度(缺列→中性 0.5)
    + 0.15 × tier_fit       # 合作等级(缺列→中性 0.5)
  )
  —— 每个分量都带 basis;未知分量走中性 0.5 并挂 note,绝不假装已知。

铁律:纯读(只 SELECT,且仅在表存在时);零 LLM / 零采集 / 零写库;
  不写 viltrox_fit_score,不引 rule_v0;compat SQL 用 ? 占位、无字面 %。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

# ── v0 权重(geo 主导,category 次之,tier 微调)──────────────────────────────
_W_GEO = 0.50
_W_CATEGORY = 0.35
_W_TIER = 0.15
_NEUTRAL = 0.5  # 未知分量的中性占位(既不奖励也不惩罚)

# ── C3 enrichment 融合(仅当 vkpi_dealer_enrichment 有该 dealer 的数据时才参与)──
# 口径:base = v0 geo/category/tier 分(向后兼容,无增强时逐字节不变);有增强且至少一个
# 子信号已知 → final = (1 - _W_ENRICH) × base + _W_ENRICH × (enrichment × 100)。
# enrichment 子分 = 已知子信号按权重求加权均值(缺的子信号不进分母,绝不假装已知)。
_W_ENRICH = 0.35  # 增强分对最终分的融合权重(v0 仍占主导)
_ENR_W_FAMILY = 0.40   # product_family_fit[family]
_ENR_W_CONV = 0.30     # conversion_proxy(线下转化代理,0..1)
_ENR_W_RESP = 0.15     # response_rate(回复率,0..1)
_ENR_W_REGION = 0.15   # region 先验(大区映射)
# 大区先验(公开市场密度常识,非真实 sell-through;未收录的 region → 不参与该子分)。
_REGION_PRIOR = {
    "north america": 1.0, "na": 1.0, "us": 1.0, "usa": 1.0, "united states": 1.0,
    "canada": 0.85, "japan": 0.85, "jp": 0.85,
    "europe": 0.8, "eu": 0.8, "emea": 0.75, "uk": 0.8, "united kingdom": 0.8,
    "apac": 0.7, "asia": 0.7, "asia pacific": 0.7, "australia": 0.7, "anz": 0.7,
    "latam": 0.6, "latin america": 0.6, "sea": 0.6, "southeast asia": 0.6,
    "mea": 0.55, "middle east": 0.55, "africa": 0.5,
}

# ── 美国相机/影像零售市场密度先验(公开常识分档,非真实销售数据)────────────
# 依据:主要人口/影视产业中心的相机零售商聚集度(公开可查的门店分布常识)。
# 这是一个「市场密度先验」,用于在无真实 sell-through 时给 geo 一个可解释的排序信号。
_GEO_TIER_A = {"CA", "NY", "TX", "FL", "IL", "WA"}  # 顶级影像零售市场
_GEO_TIER_B = {
    "MA", "CO", "OR", "GA", "PA", "NJ", "AZ", "VA", "NC", "MI", "MN", "OH", "MD", "NV",
}  # 次级市场
_GEO_VALUE_A = 1.0
_GEO_VALUE_B = 0.7
_GEO_VALUE_C = 0.45  # 其余有效州

# ── 品类归一(产品 category_main → 家族 token)────────────────────────────────
_LENS_LIKE = {"lens", "cine lens", "macro extension tube", "adapter", "uv filter"}
_CINE_LIKE = {"cine lens", "lighting", "lighting/flash", "monitor"}


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _product_family(category_main: str) -> str:
    """产品 category_main → 归一家族(lens / cine / lighting / monitor / accessory / other)。"""
    c = _lower(category_main)
    if c in ("lens", "macro extension tube"):
        return "lens"
    if c in ("cine lens",):
        return "cine"
    if c in ("lighting", "lighting/flash"):
        return "lighting"
    if c in ("monitor",):
        return "monitor"
    if c in ("adapter", "uv filter", "battery", "accessories", "product"):
        return "accessory"
    return "other"


def _dealer_category_tokens(dealer: dict[str, Any]) -> list[str]:
    """从 dealer dict 拉出品类/专长 token(容错多种字段名);无则返回 []。

    支持:categories(list 或逗号串)/ specialty / category / focus。
    真库 vkpi_dealers 无这些列 → 恒 [] → category_fit 走中性。
    """
    tokens: list[str] = []
    raw = dealer.get("categories")
    if isinstance(raw, (list, tuple)):
        tokens.extend(_lower(x) for x in raw)
    elif raw:
        tokens.extend(_lower(x) for x in str(raw).replace(";", ",").split(","))
    for key in ("specialty", "category", "focus"):
        val = dealer.get(key)
        if val:
            tokens.extend(_lower(x) for x in str(val).replace(";", ",").split(","))
    return [t for t in tokens if t]


# ── 分量评分(每个返回 (value, known, basis))──────────────────────────────────
def _geo_component(state: Any) -> tuple[float, bool, str]:
    s = str(state or "").strip().upper()
    if not s:
        return _NEUTRAL, False, "门店州缺失 → 中性(无地理先验可用)"
    if s in _GEO_TIER_A:
        return _GEO_VALUE_A, True, f"state={s} → 顶级美国影像零售市场(公开密度先验,非真实 sell-through)"
    if s in _GEO_TIER_B:
        return _GEO_VALUE_B, True, f"state={s} → 次级美国影像零售市场(公开密度先验,非真实 sell-through)"
    return _GEO_VALUE_C, True, f"state={s} → 其余市场(公开密度先验,非真实 sell-through)"


def _category_component(dealer: dict[str, Any], family: str | None) -> tuple[float, bool, str]:
    tokens = _dealer_category_tokens(dealer)
    if not tokens:
        return _NEUTRAL, False, "门店品类/专长未知(vkpi_dealers 无 category/specialty 列)→ 中性"
    if not family:
        return _NEUTRAL, False, "产品未在 vkpi_products 命中 → 无品类可比 → 中性"

    generalist = any(t in ("camera", "photo", "photography", "general", "camera store") for t in tokens)
    cine_shop = any(t in ("cine", "cinema", "video", "film", "broadcast") for t in tokens)

    # 直接家族命中(门店明确列出该品类家族)
    fam_synonyms = {
        "lens": {"lens", "lenses", "optics", "glass"},
        "cine": {"cine", "cinema", "cine lens", "video", "film"},
        "lighting": {"lighting", "light", "flash", "strobe"},
        "monitor": {"monitor", "display", "monitors"},
        "accessory": {"accessory", "accessories", "adapter", "filter", "battery"},
    }
    syns = fam_synonyms.get(family, set())
    if syns & set(tokens):
        return 1.0, True, f"门店专长直接覆盖产品家族「{family}」"
    if cine_shop and family in ("cine", "lighting", "monitor"):
        return 0.9, True, f"影视器材专营 × 产品家族「{family}」高度契合"
    if generalist:
        return 0.8, True, "综合相机零售商(通常同时经营镜头/配件)→ 强适配"
    # 有品类信息但无重合(专营他类,如无人机/胶片冲洗)
    return 0.3, True, f"门店专长与产品家族「{family}」无重合(专营他类)"


def _tier_component(dealer: dict[str, Any]) -> tuple[float, bool, str]:
    raw = _lower(dealer.get("tier") or dealer.get("partner_tier") or dealer.get("partnership"))
    if not raw:
        return _NEUTRAL, False, "合作等级未知(vkpi_dealers 无 tier 列)→ 中性"
    if raw in ("premier", "flagship", "platinum", "key", "strategic"):
        return 1.0, True, f"合作等级={raw} → 旗舰/战略"
    if raw in ("authorized", "reseller", "gold", "partner"):
        return 0.75, True, f"合作等级={raw} → 授权经销"
    if raw in ("standard", "silver", "listed"):
        return 0.6, True, f"合作等级={raw} → 标准"
    if raw in ("prospect", "lead", "unverified", "cold"):
        return 0.4, True, f"合作等级={raw} → 潜在/未验证"
    return _NEUTRAL, False, f"合作等级={raw} 无法归档 → 中性"


def _clamp01(value: Any) -> float | None:
    """数值宽容归一到 [0,1];不可解析 → None(honest 未知)。"""
    try:
        if value is None or value == "":
            return None
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _enrichment_component(
    enrichment: dict[str, Any] | None, family: str | None
) -> tuple[float, bool, str, dict[str, float]]:
    """从 dealer 增强行算融合子分(已知子信号加权均值);无数据 → 未知(不参与)。

    子信号:family_fit(product_family_fit[family])/ conversion_proxy / response_rate /
    region 先验。每个缺失就不进分母(绝不假装已知);全缺 → known=False(退回 v0)。
    """
    if not enrichment:
        return _NEUTRAL, False, "无 dealer 增强数据 → 不参与(维持 v0)", {}

    subs: list[tuple[float, float]] = []
    breakdown: dict[str, float] = {}

    fam_fit_map = enrichment.get("product_family_fit")
    if family and isinstance(fam_fit_map, dict):
        # family 大小写不敏感匹配:两侧都归一小写再比,避免上游给 'Lens' 时子信号被静默丢弃。
        family_norm = str(family).strip().lower()
        raw = fam_fit_map.get(family)
        if raw is None:
            for key, val in fam_fit_map.items():
                if str(key).strip().lower() == family_norm:
                    raw = val
                    break
        fv = _clamp01(raw)
        if fv is not None:
            subs.append((_ENR_W_FAMILY, fv))
            breakdown["family_fit"] = round(fv, 3)

    cp = _clamp01(enrichment.get("conversion_proxy"))
    if cp is not None:
        subs.append((_ENR_W_CONV, cp))
        breakdown["conversion_proxy"] = round(cp, 3)

    rr = _clamp01(enrichment.get("response_rate"))
    if rr is not None:
        subs.append((_ENR_W_RESP, rr))
        breakdown["response_rate"] = round(rr, 3)

    region = str(enrichment.get("region") or "").strip().lower()
    if region in _REGION_PRIOR:
        rv = _REGION_PRIOR[region]
        subs.append((_ENR_W_REGION, rv))
        breakdown["region"] = round(rv, 3)

    if not subs:
        return (_NEUTRAL, False,
                "增强行存在但无可用适配信号(family_fit/转化/回复/地区均缺)→ 不参与", {})

    wsum = sum(w for w, _ in subs)
    value = sum(w * v for w, v in subs) / wsum
    basis = "增强适配 = 已知子信号加权均值(" + ", ".join(sorted(breakdown)) + ")"
    return value, True, basis, breakdown


def _score_one(
    dealer: dict[str, Any], family: str | None, enrichment: dict[str, Any] | None = None
) -> dict[str, Any]:
    """单个 dealer 打分,返回带分量 basis 的可解释结果。

    enrichment 为该 dealer 的 vkpi_dealer_enrichment 行(get_enrichment_map 提供);
    无增强 / 增强无可用子信号 → 输出与 v0 逐字节一致(向后兼容)。
    """
    geo_v, geo_k, geo_b = _geo_component(dealer.get("state"))
    cat_v, cat_k, cat_b = _category_component(dealer, family)
    tier_v, tier_k, tier_b = _tier_component(dealer)

    base = 100.0 * (_W_GEO * geo_v + _W_CATEGORY * cat_v + _W_TIER * tier_v)
    notes: list[str] = []
    if not geo_k:
        notes.append("geo 未知(州缺失)")
    if not cat_k:
        notes.append("category 未知(缺 specialty 列或产品未命中)")
    if not tier_k:
        notes.append("tier 未知(缺 tier 列)")

    result: dict[str, Any] = {
        "dealer_id": dealer.get("id"),
        "name": dealer.get("name"),
        "city": dealer.get("city"),
        "state": dealer.get("state"),
        "source": dealer.get("source"),
        "dealer_fit_score": round(base, 1),
        "components": {
            "geo": {"value": round(geo_v, 3), "weight": _W_GEO, "known": geo_k, "basis": geo_b},
            "category": {"value": round(cat_v, 3), "weight": _W_CATEGORY, "known": cat_k, "basis": cat_b},
            "tier": {"value": round(tier_v, 3), "weight": _W_TIER, "known": tier_k, "basis": tier_b},
        },
        "notes": notes,
    }

    # C3 融合:仅当增强行带至少一个可用子信号时才改分(否则维持 v0 输出不动)。
    enr_v, enr_k, enr_b, enr_break = _enrichment_component(enrichment, family)
    if enr_k:
        final = (1.0 - _W_ENRICH) * base + _W_ENRICH * (enr_v * 100.0)
        result["base_fit_score"] = round(base, 1)
        result["dealer_fit_score"] = round(final, 1)
        result["components"]["enrichment"] = {
            "value": round(enr_v, 3), "weight": _W_ENRICH, "known": True,
            "basis": enr_b, "signals": enr_break,
        }
        result["enriched"] = True

    return result


# ── 产品解析(纯读 vkpi_products,仅表存在时)──────────────────────────────────
def _resolve_product(sku: str) -> dict[str, Any] | None:
    s = (sku or "").strip()
    if not s or not table_exists("vkpi_products"):
        return None
    try:
        row = get_conn().execute(
            "SELECT sku, category_main, category_detail, model_name, marketing_name, mount, price_usd, status "
            "FROM vkpi_products WHERE lower(sku)=lower(?) LIMIT 1",
            (s,),
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001 — 解析失败退化为 geo-only,不炸
        logger.warning("dealer_scoring._resolve_product failed for sku=%s: %s", s, exc)
        return None


def _load_real_dealers(limit: int = 500) -> list[dict[str, Any]]:
    """纯读真库 vkpi_dealers(表缺 → [])。"""
    if not table_exists("vkpi_dealers"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT id, name, address, city, state, lat, lng, source, created_at "
            "FROM vkpi_dealers ORDER BY id ASC LIMIT ?",
            (max(1, min(1000, int(limit or 500))),),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("dealer_scoring._load_real_dealers failed: %s", exc)
        return []


_SCORE_BASIS = (
    "v0 fit = 0.50×geo(美国影像零售市场密度先验)+ 0.35×category(门店专长×产品品类)"
    "+ 0.15×tier(合作等级);仅 geo/category 适配,**绝非**真实销售潜力(需真实 sell-through 数据)。"
)
_READY_WHEN = (
    "populate vkpi_dealers via scripts/import_dealers_stub.py(B&H/Adorama 门店定位器或品牌 dealer "
    "locator,待 Firecrawl 接入);category_fit/tier_fit 需给 vkpi_dealers 加 specialty/tier 列后才非中性。"
)


def _load_enrichment_map(dealer_ids: list[Any]) -> dict[int, dict[str, Any]]:
    """按 dealer id 批量取增强(表缺 / 零行 → {},评分退回 v0)。"""
    ids = [i for i in dealer_ids if i is not None]
    if not ids:
        return {}
    try:
        from app.domains.channel import dealer_enrichment

        return dealer_enrichment.get_enrichment_map(ids)
    except Exception as exc:  # noqa: BLE001 — 增强不可用时诚实退回 v0,绝不炸评分
        logger.warning("dealer_scoring._load_enrichment_map failed: %s", exc)
        return {}


def dealer_fit(
    sku: str,
    dealers: list[dict[str, Any]] | None = None,
    enrichment_map: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对一批 dealer 按 geo/category 适配打分并排序(纯读,可解释)。

    dealers=None → 读真库 vkpi_dealers;传入 list(fixture 或未来真行)→ 直接评分。
    enrichment_map=None → 按参评 dealer 的 id 自动读 vkpi_dealer_enrichment(表缺 → {});
    传入 map(测试或调用方已备)→ 直接用。有增强的 dealer 融合评分,无则维持 v0。
    真库 0 行 / 表缺 → status=data_missing + ready_when(绝不编数)。
    """
    product = _resolve_product(sku)
    family = _product_family(product.get("category_main", "")) if product else None
    product_view = None
    product_note = "sku 未在 vkpi_products 命中 → 仅按 geo 评分(category 中性)"
    if product:
        product_view = {
            "sku": product.get("sku"),
            "category_main": product.get("category_main"),
            "family": family,
            "model_name": product.get("model_name"),
            "mount": product.get("mount"),
        }
        product_note = "product resolved from vkpi_products"

    source_rows = dealers if dealers is not None else _load_real_dealers()
    from_db = dealers is None

    if not source_rows:
        return {
            "status": "data_missing",
            "sku": sku,
            "product": product_view,
            "product_note": product_note,
            "scored_count": 0,
            "dealers": [],
            "basis": _SCORE_BASIS,
            "note": (
                "vkpi_dealers 表存在但 0 行(硬件品牌盲区)"
                if from_db and table_exists("vkpi_dealers")
                else "vkpi_dealers 未建表" if from_db else "未提供任何 dealer 输入"
            ),
            "ready_when": _READY_WHEN,
        }

    enr_map = enrichment_map if enrichment_map is not None else _load_enrichment_map(
        [d.get("id") for d in source_rows]
    )
    scored = [_score_one(d, family, enr_map.get(d.get("id"))) for d in source_rows]
    # 排序:分数降序,稳定次序按 name(可复现)。
    scored.sort(key=lambda r: (-(r["dealer_fit_score"] or 0.0), str(r.get("name") or "")))
    enriched_count = sum(1 for r in scored if r.get("enriched"))

    result = {
        "status": "ok",
        "sku": sku,
        "product": product_view,
        "product_note": product_note,
        "scored_count": len(scored),
        "source": "vkpi_dealers(real)" if from_db else "provided(fixture/rows)",
        "dealers": scored,
        "basis": _SCORE_BASIS,
        "limitations": [
            "geo 分量是市场密度先验,非真实 sell-through",
            "真库 vkpi_dealers 无 category/tier 列 → 该二分量对真行恒中性",
            "v0 只做 geo/category 适配,不估算销售潜力",
        ],
    }
    # 有增强参评时才挂计数(无增强 → 输出与 v0 一致,向后兼容)。
    if enriched_count:
        result["enriched_count"] = enriched_count
    return result


def dealer_targets(sku: str, limit: int = 50) -> dict[str, Any]:
    """SKU 的 Dealer 目标榜(读真库 vkpi_dealers)。

    0 行 / 表缺 → status=data_missing + ready_when(诚实空态,绝不编数)。
    有行 → status=ok,返回 dealer_fit 排序后的 top-N 目标。
    """
    if not table_exists("vkpi_dealers"):
        return {
            "status": "data_missing",
            "sku": sku,
            "targets": [],
            "count": 0,
            "note": "vkpi_dealers 未建表(迁移 144 未应用)",
            "ready_when": _READY_WHEN,
            "basis": _SCORE_BASIS,
        }

    result = dealer_fit(sku)  # 内部读真库
    if result.get("status") == "data_missing":
        return {
            "status": "data_missing",
            "sku": sku,
            "product": result.get("product"),
            "targets": [],
            "count": 0,
            "note": result.get("note"),
            "ready_when": _READY_WHEN,
            "basis": _SCORE_BASIS,
        }

    dealers = result.get("dealers", [])
    top = dealers[: max(1, min(200, int(limit or 50)))]
    out = {
        "status": "ok",
        "sku": sku,
        "product": result.get("product"),
        "product_note": result.get("product_note"),
        "count": len(dealers),
        "targets": top,
        "basis": _SCORE_BASIS,
        "limitations": result.get("limitations", []),
    }
    if result.get("enriched_count"):
        out["enriched_count"] = result["enriched_count"]
    return out
