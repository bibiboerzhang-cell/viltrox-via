"""地基A 产品 persona 离线批跑(2026-06-13)。

读 vkpi_products(sku/model_name/marketing_name/category_main/series/price_usd/description
+ specs_json 真参数),逐 SKU 用 llm_gateway.invoke(preferred_provider=anthropic 即 Claude,
或 openai;绝不用 google/gemini-flash——会截断深度产品分析)注入真 specs → 产结构化
persona / 推广方向 → upsert vkpi_product_persona。

红线:
- LLM 只产产品 persona 文本/标签;绝不写 fit、绝不写任何评分列。
- viltrox_fit_score 唯一写点仍是 pool.py enrich_item;rule_v0 打分公式冻结,本脚本零触。
- 预算走 $1000/月闸(LLM_MONTHLY_BUDGET_USD + budget_guard);单 SKU 失败跳过不崩。
- DB 写走应用路径(db_connection_sync_scope + get_conn);不 push / 不 git add -A。

用法(须用 .venv —— MEMORY:裸 python3 缺 boto3 会让 R2 静默降级):
    cd backend && ../.venv/bin/python scripts_local/build_product_personas.py                        # dry-run 看样例
    cd backend && ../.venv/bin/python scripts_local/build_product_personas.py --limit 3              # 只看前 3 个 SKU 样例
    cd backend && ../.venv/bin/python scripts_local/build_product_personas.py --only-missing          # 只列尚无 persona 的 SKU
    cd backend && ../.venv/bin/python scripts_local/build_product_personas.py --execute --only-missing # 全量补齐落库
    cd backend && ../.venv/bin/python scripts_local/build_product_personas.py --sku EPIC-65-MACRO      # 单 SKU 调试

默认 dry-run(打印 LLM 产出的 persona 样例,不落库)——镜像 compute_real_er / compute_suspect_inflation 约定。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.connection import db_connection_sync_scope, get_conn  # noqa: E402
from app.domains.costs import product_persona as persona_helper  # noqa: E402
from app.platform import llm_gateway  # noqa: E402

PERSONA_SOURCE = "llm_persona_v1"
# Claude(anthropic)首选 → openai 兜底;绝不用 google/gemini-flash(深度产品分析会截断)。
PREFERRED_PROVIDER = "openai"  # GPT:走代理可达、长 specs 上下文稳;anthropic 本环境被墙、google=flash 截断
PERSONA_COST_TAG = "vkpi_product_persona"
MAX_OUTPUT_TOKENS = 1100

_JSON_LIST_FIELDS = ("ideal_creator_types", "verticals", "promotion_angles", "avoid_types")


def _parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "execute": "--execute" in argv,
        "only_missing": "--only-missing" in argv,
        "limit": None,
        "sku": None,
    }
    for idx, token in enumerate(argv):
        if token == "--limit" and idx + 1 < len(argv):
            try:
                opts["limit"] = max(1, int(argv[idx + 1]))
            except ValueError:
                pass
        elif token.startswith("--limit="):
            try:
                opts["limit"] = max(1, int(token.split("=", 1)[1]))
            except ValueError:
                pass
        elif token == "--sku" and idx + 1 < len(argv):
            opts["sku"] = str(argv[idx + 1]).strip()
        elif token.startswith("--sku="):
            opts["sku"] = token.split("=", 1)[1].strip()
    return opts


def _load_products(opts: dict[str, Any]) -> list[dict[str, Any]]:
    conn = get_conn()
    where: list[str] = []
    params: list[Any] = []
    if opts.get("sku"):
        where.append("p.sku = ?")
        params.append(opts["sku"])
    if opts.get("only_missing"):
        where.append("pp.product_sku IS NULL")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT p.sku, p.model_name, p.marketing_name, p.category_main, p.series,
               p.price_usd, p.description, p.specs_json
        FROM vkpi_products p
        LEFT JOIN vkpi_product_persona pp ON pp.product_sku = p.sku
        {clause}
        ORDER BY
          CASE p.category_main
            WHEN 'Cine Lens' THEN 0
            WHEN 'Lens' THEN 1
            WHEN 'Monitor' THEN 2
            WHEN 'Lighting/Flash' THEN 3
            WHEN 'Lighting' THEN 4
            WHEN 'Macro Extension Tube' THEN 5
            ELSE 9
          END ASC,
          p.category_main ASC, p.sku ASC
    """
    rows = conn.execute(sql, tuple(params)).fetchall()
    products = [dict(row) for row in rows]
    if opts.get("limit"):
        products = products[: opts["limit"]]
    return products


def _real_specs(product: dict[str, Any]) -> dict[str, Any]:
    """Pull real, non-hallucinated specs straight from vkpi_products to inject into the prompt."""
    specs: dict[str, Any] = {}
    raw = product.get("specs_json")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, dict):
                specs.update(parsed)
        except (TypeError, ValueError):
            pass
    if product.get("price_usd") is not None and "price_usd" not in specs:
        specs["price_usd"] = product["price_usd"]
    if product.get("series"):
        specs.setdefault("series", product["series"])
    return specs


def _build_prompt(product: dict[str, Any], specs: dict[str, Any]) -> str:
    name = product.get("marketing_name") or product.get("model_name") or product.get("sku")
    specs_text = json.dumps(specs, ensure_ascii=False, indent=2, default=str) if specs else "(无结构化参数,仅凭描述)"
    return (
        "你是 Viltrox(唯卓仕)影像产品的市场推广策略师。基于下方真实产品资料,"
        "为这个 SKU 产出一份「推广方向 persona」:这个产品是什么、最适合哪类创作者、"
        "用什么角度推广、明确要规避哪类错配创作者。只用提供的真实参数,不要杜撰规格。\n\n"
        f"产品名:{name}\n"
        f"SKU:{product.get('sku')}\n"
        f"主类目:{product.get('category_main')}\n"
        f"系列:{product.get('series') or '(无)'}\n"
        f"参考价(USD):{product.get('price_usd') if product.get('price_usd') is not None else '(未定价)'}\n"
        f"真实参数(specs,权威,请据此判断焦段/光圈/卡口/价位/专业级别):\n{specs_text}\n"
        f"官方描述:{(product.get('description') or '').strip()[:1200]}\n\n"
        "严格只输出一个 JSON 对象(不要 markdown 代码块、不要解释),字段如下:\n"
        "{\n"
        '  "what_is": "一句话:这个产品是什么(中文)",\n'
        '  "key_specs": {"焦段": "...", "光圈": "...", "卡口": "...", "价位": "...", "专业级别": "..."},\n'
        '  "ideal_persona": "一段话(中文):理想创作者人群画像与为何合适",\n'
        '  "ideal_creator_types": ["创作者类型1", "类型2"],\n'
        '  "verticals": ["垂类1", "垂类2"],\n'
        '  "promotion_angles": ["推广方向/卖点角度1", "角度2"],\n'
        '  "avoid_types": ["明确规避的错配创作者类型1", "类型2"]\n'
        "}\n"
        "key_specs 的值必须来自上面提供的真实参数;不确定就留空字符串,绝不编造规格。"
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    body = str(text or "").strip()
    if not body:
        return None
    if body.startswith("```"):
        body = body.strip("`")
        if body.lower().startswith("json"):
            body = body[4:]
        body = body.strip()
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        pass
    start = body.find("{")
    end = body.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(body[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except (TypeError, ValueError):
            return None
    return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_persona(parsed: dict[str, Any], specs: dict[str, Any]) -> dict[str, Any]:
    key_specs = parsed.get("key_specs")
    if not isinstance(key_specs, dict):
        key_specs = {}
    # 真实参数快照以 vkpi_products 为准入库,LLM 的 key_specs 仅作可读补充层。
    merged_specs = {**specs, **{str(k): v for k, v in key_specs.items()}}
    persona = {
        "what_is": str(parsed.get("what_is") or "").strip(),
        "key_specs_json": merged_specs,
        "ideal_persona": str(parsed.get("ideal_persona") or "").strip(),
    }
    for field in _JSON_LIST_FIELDS:
        persona[f"{field}_json"] = _as_list(parsed.get(field))
    return persona


def _upsert(conn: Any, sku: str, category: str, persona: dict[str, Any], model: str) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_product_persona
            (product_sku, category, what_is, key_specs_json, ideal_persona,
             ideal_creator_types_json, verticals_json, promotion_angles_json,
             avoid_types_json, model, source, generated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
        ON CONFLICT (product_sku) DO UPDATE SET
            category = EXCLUDED.category,
            what_is = EXCLUDED.what_is,
            key_specs_json = EXCLUDED.key_specs_json,
            ideal_persona = EXCLUDED.ideal_persona,
            ideal_creator_types_json = EXCLUDED.ideal_creator_types_json,
            verticals_json = EXCLUDED.verticals_json,
            promotion_angles_json = EXCLUDED.promotion_angles_json,
            avoid_types_json = EXCLUDED.avoid_types_json,
            model = EXCLUDED.model,
            source = EXCLUDED.source,
            generated_at = CURRENT_TIMESTAMP
        """,
        (
            sku,
            category,
            persona["what_is"],
            json.dumps(persona["key_specs_json"], ensure_ascii=False),
            persona["ideal_persona"],
            json.dumps(persona["ideal_creator_types_json"], ensure_ascii=False),
            json.dumps(persona["verticals_json"], ensure_ascii=False),
            json.dumps(persona["promotion_angles_json"], ensure_ascii=False),
            json.dumps(persona["avoid_types_json"], ensure_ascii=False),
            model or "",
            PERSONA_SOURCE,
        ),
    )
    conn.commit()


def _print_sample(sku: str, persona: dict[str, Any], provider: str, model: str) -> None:
    print(f"\n=== {sku}  (provider={provider} model={model}) ===")
    print(f"what_is        : {persona['what_is']}")
    print(f"ideal_persona  : {persona['ideal_persona'][:300]}")
    print(f"creator_types  : {persona['ideal_creator_types_json']}")
    print(f"verticals      : {persona['verticals_json']}")
    print(f"promo_angles   : {persona['promotion_angles_json']}")
    print(f"avoid_types    : {persona['avoid_types_json']}")


def main(argv: list[str]) -> None:
    opts = _parse_args(argv)
    execute = bool(opts["execute"])
    with db_connection_sync_scope():
        # sqlite 本地 guard:确保表存在,离线 dry-run 不崩(Postgres 由 migration 119 建表)。
        persona_helper._ensure_sqlite_guard()
        conn = get_conn()
        products = _load_products(opts)
        if not products:
            print("没有匹配的 SKU(检查 --sku / --only-missing 过滤,或 vkpi_products 是否为空)。")
            return
        mode = "EXECUTE" if execute else "DRY-RUN"
        print(
            f"[{mode}] 待处理 SKU={len(products)} preferred_provider={PREFERRED_PROVIDER} "
            f"cost_tag={PERSONA_COST_TAG}(预算走 $1000/月闸,绝不用 gemini-flash)"
        )
        done = skipped = 0
        for product in products:
            sku = str(product.get("sku") or "").strip()
            if not sku:
                skipped += 1
                continue
            try:
                specs = _real_specs(product)
                prompt = _build_prompt(product, specs)
                # provider 间歇掉线(~50%),重试至多 3 次到 success+text(纯重试,不绕预算闸)。
                result = {}
                for _attempt in range(3):
                    result = llm_gateway.invoke(
                        prompt,
                        purpose="vkpi_product_persona",
                        preferred_provider=PREFERRED_PROVIDER,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                        cost_tag=PERSONA_COST_TAG,
                        metadata={"sku": sku, "category": product.get("category_main"), "attempt": _attempt + 1},
                    )
                    if str(result.get("provider") or "") != "rule_v0" and str(result.get("text") or "").strip():
                        break
                provider = str(result.get("provider") or "")
                if provider == "rule_v0" or not str(result.get("text") or "").strip():
                    print(f"SKIP {sku} :: LLM 无产出(provider={provider} status={result.get('status')})——跳过不落库")
                    skipped += 1
                    continue
                parsed = _extract_json(result.get("text"))
                if not parsed:
                    print(f"SKIP {sku} :: LLM 输出非 JSON,无法解析——跳过不落库")
                    skipped += 1
                    continue
                persona = _normalize_persona(parsed, specs)
                model = str(result.get("model") or "")
                if execute:
                    _upsert(conn, sku, str(product.get("category_main") or ""), persona, model)
                    done += 1
                    print(f"OK   {sku} :: upsert vkpi_product_persona (provider={provider} model={model})")
                else:
                    _print_sample(sku, persona, provider, model)
                    done += 1
            except Exception as exc:  # 单 SKU 失败跳过不崩
                print(f"ERR  {sku} :: {type(exc).__name__}: {str(exc)[:200]}——跳过")
                skipped += 1
                continue
        print(f"\n[{mode}] done={done} skipped={skipped} total={len(products)}")
        if not execute:
            print("dry-run 未落库。确认样例后加 --execute 落库(只产 persona 文本/标签,零触 viltrox_fit_score)。")


if __name__ == "__main__":
    main(sys.argv[1:])
