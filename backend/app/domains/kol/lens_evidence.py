"""镜头出镜证据抽取(final_v1 深析缓存 → Viltrox 产品提及 → 目录归一)。

输入:vkpi_analysis_cache.result(derive_method=video_analysis_final_v1)。六层结果里
产品识别只存在于散文字段(layer1.product_presence / brand_exposure / content_summary /
scene_timeline、layer4.product_contribution、layer6.product_proof_score.evidence、
raw.content_topic 等);结构化 viltrox_products_all 在存量里全空,所以这里用
「品牌词锚定 + 型号 token 截取」的确定性抽取,零 LLM、零外调。

归一:只认 vkpi_products 目录(+ vkpi_product_aliases 别名表 + products/product_aliases_lens
口语别名表);
  * sku        —— 唯一命中一个 SKU(含卡口);
  * family     —— 命中同一镜头家族但卡口未知 / 目录无该卡口 → 多 SKU 候选;仅系列提及(Pro / Air / EVO / LAB / EPIC)同归 family(lens_key=series:xxx);
  * unresolved —— 目录无此型号 / 多家族歧义 → 保留原文,绝不杜撰。
modality(画面 / 字幕·文字 / 口播)按提及所在句子的线索词判定,判不出 = unspecified。
v_relevance 三态只读投影(v_relevance_for):
  * confirmed —— 归一到目录(sku/family)且画面 / 口播 / 字幕明确提及;
  * likely    —— 提及但未归一 / 仅系列 / 归一了但无明确 modality / 只出现在建议性字段;
  * none      —— 整条深析零提及(视频级,由扫描账本给出)。
红线:纯读;绝不写 viltrox_fit_score / 不触 rule_v0。
截取/规格/家族/目录零件的实现住在 lens_evidence_parts.py(CC 战役 2026-08-30 平移,
行为逐字节不变);本模块保留抽取编排、目录索引与全部历史名字。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domains.kol import lens_evidence_parts as parts
from app.domains.kol.lens_evidence_parts import (  # noqa: F401  (历史名字保留给调用方/测试)
    CatalogProduct,
    _clip,
    _looks_like_product,
    _mount_code,
    _mount_hint,
    _normkey,
    _text,
    canonical_text,
    family_name,
    parse_spec,
    split_slash_list,
)
from app.domains.products.product_aliases import generated_aliases_for_product, normalize_alias
from app.domains.products.product_aliases_lens import (
    ALIAS_TABLE_VERSION,
    lookup_lens_alias,
    series_label,
)


EXTRACTOR_VERSION = "lens_evidence_v2"
FINAL_DERIVE_METHOD = "video_analysis_final_v1"
RESOLUTIONS = ("sku", "family", "unresolved")
MODALITIES = ("visual", "text", "voice", "unspecified")
V_RELEVANCE = ("confirmed", "likely", "none")
V_RELEVANCE_LABELS = {"confirmed": "确认出镜", "likely": "疑似出镜", "none": "无出镜"}
EXPLICIT_MODALITIES = frozenset({"visual", "voice", "text"})
# 建议性字段(裁决 / 钩子)里的点名不是出镜证据,只能算 likely。
ADVISORY_FIELDS = frozenset({"final_verdict", "key_hook"})
# 仅系列提及只认「产品出镜」类字段;裁决 / 归因里的「推 Pro 系列」是建议不是证据。
SERIES_SOURCE_FIELDS = frozenset({"product_presence", "brand_exposure", "scene_timeline", "content_summary", "raw_content_topic", "raw_viltrox_lens", "raw_viltrox_products_all"})
MAX_MENTIONS_PER_CACHE = 12
MAX_CANDIDATES = 12
SERIES_KEY_PREFIX = "series:"

_BRAND_RE = re.compile(
    r"(?:viltrox|唯卓仕|唯卓|维卓仕|维卓)\s*(?:的|品牌的|品牌|全新|新品|新款|这款|这支|那支|那款|出品的)*[\s:：,，]*",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[。；;！？!?\n]+|(?<=[a-z0-9\)])\.\s+")
_MODALITY_CUES: dict[str, re.Pattern[str]] = {
    "text": re.compile(
        r"字幕|标题|文字|标注|标明|标识|标示|水印|\bui\b|文本|简介|贴纸|角标|左下角|右下角|caption|subtitle|title|overlay|on-?screen text|watermark|description|label",
        re.IGNORECASE,
    ),
    "visual": re.compile(
        r"特写|出镜|出现|展示|手持|画面|可见|开箱|实拍|镜头特写|搭载|装在|挂在|拿着|示范|演示|露出|贯穿|上手|外观|"
        r"close-?ups?|shown|appears?|visible|on[- ]screen|unbox|holding|mounted|footage|b-?roll|showcase|displayed|seen",
        re.IGNORECASE,
    ),
    "voice": re.compile(
        r"口播|口述|旁白|讲解|说到|说明|介绍|念出|谈到|口头|语音|narrat|voice|spoken|says?|said|talks?|explain|discuss",
        re.IGNORECASE,
    ),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_result(value: Any) -> dict[str, Any]:
    """compat 把 jsonb 读回成字符串;这里统一成 dict。"""

    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def final_payload(result: Any) -> dict[str, Any]:
    root = load_result(result)
    nested = _as_dict(root.get(FINAL_DERIVE_METHOD))
    if _as_dict(nested.get("layer1_visual_content")):
        return nested
    raw_nested = _as_dict(_as_dict(root.get("raw_gemini_video")).get(FINAL_DERIVE_METHOD))
    if not _as_dict(root.get("layer1_visual_content")) and _as_dict(raw_nested.get("layer1_visual_content")):
        return raw_nested
    return root


def _flatten_strings(value: Any, limit: int = 40) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [value]
    while stack and len(out) < limit:
        current = stack.pop()
        if isinstance(current, str):
            if current.strip():
                out.append(current)
        elif isinstance(current, dict):
            stack.extend(list(current.values())[::-1])
        elif isinstance(current, list):
            stack.extend(current[::-1])
    return out


def source_texts(result: Any) -> list[tuple[str, str]]:
    """(source_field, text) 列表;只取会点名产品的字段,不吞整份结果。"""

    payload = final_payload(result)
    root = load_result(result)
    raw = _as_dict(root.get("raw_gemini_video"))
    layer1 = _as_dict(payload.get("layer1_visual_content"))
    layer4 = _as_dict(payload.get("layer4_attribution"))
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    pairs: list[tuple[str, str]] = []

    def add(field_name: str, value: Any) -> None:
        for text in _flatten_strings(value):
            pairs.append((field_name, text))

    add("product_presence", layer1.get("product_presence"))
    add("brand_exposure", layer1.get("brand_exposure"))
    add("content_summary", layer1.get("content_summary"))
    for scene in _as_list(layer1.get("scene_timeline")):
        if isinstance(scene, dict):
            add("scene_timeline", scene.get("what"))
    add("product_contribution", layer4.get("product_contribution"))
    for item in _as_list(layer4.get("attribution_breakdown")):
        if isinstance(item, dict):
            add("attribution_breakdown", item.get("evidence"))
    scores = _as_dict(layer6.get("scores"))
    add("product_proof_score", _as_dict(scores.get("product_proof_score")).get("evidence"))
    add("key_hook", layer6.get("key_hook"))
    add("final_verdict", layer6.get("final_verdict"))
    add("raw_content_topic", raw.get("content_topic"))
    add("raw_viltrox_lens", raw.get("viltrox_lens"))
    add("raw_viltrox_products_all", raw.get("viltrox_products_all"))
    return pairs


# ── 提及截取(实现在 lens_evidence_parts,老名字顶部引入)────────────────────


def _clip_body(after_brand: str) -> str:
    return _clip(after_brand)[0]


def normalize_mention(value: str) -> str:
    text = canonical_text(_text(value).replace("full- frame", "full frame")).lower()
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    tokens = [token for token in text.split() if token not in {"af", "lens", "mount", "full", "frame", "apsc", "aps", "c"}]
    return " ".join(tokens)


def _sentence_for(text: str, start: int, end: int) -> str:
    pieces = [(m.start(), m.end()) for m in _SENTENCE_SPLIT_RE.finditer(text)]
    left = 0
    right = len(text)
    for s_start, s_end in pieces:
        if s_end <= start:
            left = s_end
        elif s_start >= end:
            right = s_start
            break
    return text[left:right]


def detect_modalities(context: str) -> list[str]:
    found = [name for name in ("text", "visual", "voice") if _MODALITY_CUES[name].search(context or "")]
    return found or ["unspecified"]


@dataclass
class Mention:
    text: str
    norm: str
    modalities: set[str] = field(default_factory=set)
    source_fields: set[str] = field(default_factory=set)
    count: int = 0
    series: str = ""
    from_list: bool = False


_CONTEXT_WHOLE_FIELDS = frozenset({"product_presence", "brand_exposure", "raw_viltrox_lens", "raw_viltrox_products_all"})


def extract_mentions(result: Any) -> list[Mention]:
    """从一份 final_v1 结果抽 Viltrox 产品提及(按 mention_norm 去重,合并线索)。

    型号提及:品牌词锚定 + token 截取(斜杠列表拆成多条);
    仅系列提及:只从产品出镜类字段(SERIES_SOURCE_FIELDS)里取,norm=系列码,series 字段标记。
    """

    found: dict[str, Mention] = {}

    def record(norm: str, body: str, context: str, field_name: str, series: str = "", from_list: bool = False) -> None:
        item = found.get(norm)
        if item is None:
            item = Mention(text=body, norm=norm, series=series, from_list=from_list)
            found[norm] = item
        elif len(body) > len(item.text) and not series:
            item.text = body
        item.modalities.update(detect_modalities(context))
        item.source_fields.add(field_name)
        item.count += 1

    def handle_match(field_name: str, text: str, match: re.Match) -> None:
        body, series_codes = _clip(text[match.end():match.end() + 120])
        if not body and not series_codes:
            return
        context = text if field_name in _CONTEXT_WHOLE_FIELDS else _sentence_for(text, match.start(), match.end() + len(body or "") + 8)
        if not body:
            if field_name not in SERIES_SOURCE_FIELDS:
                return
            for code in series_codes:
                record(f"{SERIES_KEY_PREFIX}{code.lower()}", series_label(code), context, field_name, series=code)
            return
        pieces = split_slash_list(body)
        for piece in pieces:
            norm = normalize_mention(piece)
            if norm:
                record(norm, piece, context, field_name, from_list=len(pieces) > 1)

    for field_name, text in source_texts(result):
        for match in _BRAND_RE.finditer(text):
            handle_match(field_name, text, match)
            if len(found) >= MAX_MENTIONS_PER_CACHE * 2:
                break
    for item in found.values():
        if len(item.modalities) > 1:
            item.modalities.discard("unspecified")
    return list(found.values())


# ── 目录归一 ────────────────────────────────────────────────────────────────


class CatalogIndex:
    """一次装目录(vkpi_products + 别名),内存里反复归一。"""

    def __init__(self, products: Iterable[dict[str, Any]], aliases: Iterable[dict[str, Any]] | None = None) -> None:
        self.products: dict[str, CatalogProduct] = {}
        self.alias_map: dict[str, set[str]] = {}
        for row in products:
            product = parts.catalog_product(row)
            if product is None:
                continue
            self.products[product.sku] = product
            self.alias_map.setdefault(_normkey(product.sku), set()).add(product.sku)
            self.alias_map.setdefault(_normkey(product.model_name), set()).add(product.sku)
            self.alias_map.setdefault(_normkey(product.family), set()).add(product.sku)
        for row in self._alias_rows(products, aliases):
            sku = _text(row.get("sku")).upper()
            key = _normkey(row.get("alias_norm") or normalize_alias(row.get("alias")))
            if sku in self.products and key:
                self.alias_map.setdefault(key, set()).add(sku)

    @staticmethod
    def _alias_rows(
        products: Iterable[dict[str, Any]], aliases: Iterable[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        alias_rows = list(aliases) if aliases is not None else []
        if not alias_rows:
            for row in products:
                alias_rows.extend(generated_aliases_for_product(dict(row)))
        return alias_rows

    def _outcome(self, skus: set[str], mention: str, *, force_family: bool = False, note: str = "") -> dict[str, Any]:
        # 旧成本目录(VL-xxx)是官方目录的重复登记(无系列/卡口);两边都命中时只认官方行。
        official = {sku for sku in skus if not sku.upper().startswith("VL-")}
        candidates = sorted(official or skus)[:MAX_CANDIDATES]
        if not candidates:
            return {"resolution": "unresolved", "product_sku": None, "lens_key": "", "display_name": mention, "category_main": "", "candidate_skus": [], "note": note}
        if len(candidates) == 1 and not force_family:
            product = self.products[candidates[0]]
            return {"resolution": "sku", "product_sku": product.sku, "lens_key": product.family_key, "display_name": product.family, "category_main": product.category_main, "candidate_skus": candidates, "note": note}
        families = {self.products[sku].family_key for sku in candidates}
        if len(families) == 1:
            product = self.products[candidates[0]]
            return {"resolution": "family", "product_sku": None, "lens_key": product.family_key, "display_name": product.family, "category_main": product.category_main, "candidate_skus": candidates, "note": note}
        return {"resolution": "unresolved", "product_sku": None, "lens_key": "", "display_name": mention, "category_main": "", "candidate_skus": candidates, "note": note}

    def series_outcome(self, code: str) -> dict[str, Any]:
        """仅系列提及:归 family(系列键 series:xxx + 该系列候选 SKU,上限 MAX_CANDIDATES),不猜型号。"""

        wanted = _text(code).lower()
        skus = sorted(
            p.sku for p in self.products.values()
            if not p.sku.startswith("VL-") and (wanted in p.spec["series"] or _text(p.series).lower() == wanted)
        )
        categories = {self.products[sku].category_main for sku in skus}
        return {
            "resolution": "family" if skus else "unresolved",
            "product_sku": None,
            "lens_key": f"{SERIES_KEY_PREFIX}{wanted}",
            "display_name": series_label(code),
            "category_main": next(iter(categories)) if len(categories) == 1 else "",
            "candidate_skus": skus[:MAX_CANDIDATES],
            "note": "series_only",
        }

    def _apply_mount(self, pool: list[CatalogProduct], mount: str) -> tuple[list[CatalogProduct], bool]:
        """卡口硬约束;目录没有该卡口时退回家族级(force_family),绝不猜 SKU。"""

        if not mount or not pool:
            return pool, False
        wanted = {mount}
        if mount in {"PL-mount", "L-mount"}:
            wanted = {"PL-mount", "L-mount"}
        narrowed = [p for p in pool if p.mount in wanted]
        if narrowed:
            return narrowed, False
        return pool, True

    def _resolve_spec(self, mention_text: str, spec: dict[str, Any], hit: set[str]) -> dict[str, Any]:
        pool = parts.focal_pool(self.products, hit, spec)
        pool = parts.narrow_by_aperture(pool, spec)
        pool = parts.narrow_by_series(pool, spec)
        pool, mount_unmatched = self._apply_mount(pool, spec["mount"])
        pool = parts.prefer_official(pool)
        pool = parts.prefer_plain_family(pool, spec)
        return self._outcome({p.sku for p in pool}, mention_text, force_family=mount_unmatched, note="mount_unmatched" if mount_unmatched else "")

    def _resolve_alias(self, mention_text: str, canonical: str, spec: dict[str, Any]) -> dict[str, Any]:
        """口语别名 → canonical 家族名;再按原提及里的卡口收窄。canonical 不在目录 = 照旧 unresolved。"""

        hit = set(self.alias_map.get(_normkey(canonical)) or ())
        hit |= set(self.alias_map.get(_normkey(family_name(canonical))) or ())
        # 非镜头(适配器 / 灯 / 监视器)的型号码本身就含 EF / PL / E 字样,卡口约束只对镜头生效。
        mount = spec["mount"] if parse_spec(canonical)["focal"] else ""
        if hit:
            pool, mount_unmatched = self._apply_mount([self.products[sku] for sku in sorted(hit)], mount)
            outcome = self._outcome({p.sku for p in pool}, mention_text, force_family=mount_unmatched, note="mount_unmatched" if mount_unmatched else "")
        else:
            canonical_spec = parse_spec(canonical)
            if not canonical_spec["focal"]:
                return self._outcome(set(), mention_text)
            canonical_spec["mount"] = mount
            outcome = self._resolve_spec(mention_text, canonical_spec, set())
        outcome["note"] = parts.merged_note(outcome.get("note"), "alias_table")
        return outcome

    def _resolve_token_probe(self, probe: str, mention_text: str) -> dict[str, Any] | None:
        """非镜头型号码(DC-A1 / Z1 Pro / K60):型号 token 子串命中。"""
        pool = [p for p in self.products.values() if parts.probe_hits(p, probe)]
        if not pool:
            return None
        exact = [p for p in pool if parts.probe_exact(p, probe)]
        return self._outcome({p.sku for p in (exact or pool)}, mention_text)

    def _exact_alias_hit(self, mention_text: str, key: str) -> set[str]:
        return set(self.alias_map.get(key) or self.alias_map.get(_normkey(normalize_alias(mention_text))) or ())

    def resolve(self, mention: str) -> dict[str, Any]:
        mention_text = _text(mention)
        if not mention_text:
            return self._outcome(set(), mention_text)
        # 1. 别名 / 家族名精确命中(镜头还要并上规格匹配,别让旧目录别名抢先定案)
        key = _normkey(mention_text)
        hit = self._exact_alias_hit(mention_text, key)
        spec = parse_spec(mention_text)
        if hit and not spec["focal"]:
            return self._outcome(hit, mention_text)
        # 2. 口语别名表(数据驱动):「85 1.4」「135 LAB」「Z2」→ canonical 家族,再过目录
        canonical = lookup_lens_alias(mention_text)
        if canonical:
            outcome = self._resolve_alias(mention_text, canonical, spec)
            if outcome["resolution"] != "unresolved":
                return outcome
        # 3. 镜头规格匹配:焦段必须全等;光圈 / 系列 / 卡口有就当硬约束
        if spec["focal"]:
            return self._resolve_spec(mention_text, spec, hit)
        # 4. 非镜头型号码:型号 token 子串命中
        if len(key) >= 3:
            probed = self._resolve_token_probe(key, mention_text)
            if probed is not None:
                return probed
        return self._outcome(set(), mention_text)


def load_catalog_index(conn: Any) -> CatalogIndex:
    products = [dict(row) for row in conn.execute(
        "SELECT sku, model_name, marketing_name, category_main, series, mount, specs_json, fit_tags_json, product_url FROM vkpi_products"
    ).fetchall()]
    aliases: list[dict[str, Any]] = []
    try:
        aliases = [dict(row) for row in conn.execute(
            "SELECT sku, alias, alias_norm FROM vkpi_product_aliases"
        ).fetchall()]
    except Exception:
        aliases = []
    return CatalogIndex(products, aliases or None)


def _retry_without_list_suffix(index: CatalogIndex, mention: Mention, outcome: dict[str, Any]) -> dict[str, Any]:
    # 「13mm/23mm/27mm/75mm Pro」:列表尾巴的系列词未必属于每一项,去掉再试一次(仍过目录)。
    bare = re.sub(r"\b(?:pro|air|evo|lab|ii)\b", " ", mention.text, flags=re.IGNORECASE).strip()
    if not bare or bare == mention.text:
        return outcome
    retry = index.resolve(canonical_text(bare))
    if retry["resolution"] == "unresolved":
        return outcome
    retry["note"] = parts.merged_note(retry.get("note"), "list_suffix_dropped")
    return retry


def _group_key(outcome: dict[str, Any], mention: Mention) -> str:
    return (
        f"sku:{outcome['product_sku']}" if outcome["resolution"] == "sku"
        else f"family:{outcome['lens_key']}" if outcome["resolution"] == "family"
        else f"raw:{mention.norm}"
    )


def _new_merged_row(mention: Mention, outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "mention_text": mention.text,
        "mention_norm": mention.norm,
        "resolution": outcome["resolution"],
        "product_sku": outcome["product_sku"],
        "lens_key": outcome["lens_key"],
        "display_name": outcome["display_name"],
        "category_main": outcome["category_main"],
        "candidate_skus": list(outcome["candidate_skus"]),
        "modalities": set(),
        "source_fields": set(),
        "mention_count": 0,
        "note": _text(outcome.get("note")),
    }


def _host_for(row: dict[str, Any], resolved_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    tokens = set(row["mention_norm"].split())
    for candidate in resolved_rows:
        host_tokens = set(normalize_mention(candidate["display_name"]).split()) | set(candidate["mention_norm"].split())
        if tokens and tokens <= host_tokens:
            return candidate
    return None


def _absorb_unresolved(merged: dict[str, dict[str, Any]]) -> None:
    """同一条结果里「85mm」「Z2」这类残缺提及,若已有同焦段 / 同型号码的归一行,并进去。"""
    resolved_rows = [row for row in merged.values() if row["resolution"] != "unresolved"]
    for key in [k for k in merged if k.startswith("raw:")]:
        row = merged[key]
        host = _host_for(row, resolved_rows)
        if host is None:
            continue
        host["modalities"].update(row["modalities"])
        host["source_fields"].update(row["source_fields"])
        host["mention_count"] += row["mention_count"]
        del merged[key]


def _finalize_rows(merged: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in merged.values():
        modalities = {m for m in row["modalities"] if m in MODALITIES}
        if len(modalities) > 1:
            modalities.discard("unspecified")
        row["modalities"] = sorted(modalities or {"unspecified"})
        row["source_fields"] = sorted(row["source_fields"])
        row["v_relevance"], row["v_reason"] = v_relevance_for(row)
        out.append(row)
    return out


def extract_resolved(result: Any, index: CatalogIndex) -> list[dict[str, Any]]:
    """抽取 + 归一;同家族 / 同 SKU 的多次提及合并成一行(unresolved 按原文去重)。"""

    merged: dict[str, dict[str, Any]] = {}
    for mention in extract_mentions(result):
        outcome = index.series_outcome(mention.series) if mention.series else index.resolve(mention.text)
        if outcome["resolution"] == "unresolved" and mention.from_list:
            outcome = _retry_without_list_suffix(index, mention, outcome)
        group_key = _group_key(outcome, mention)
        row = merged.get(group_key)
        if row is None:
            row = _new_merged_row(mention, outcome)
            merged[group_key] = row
        row["modalities"].update(mention.modalities)
        row["source_fields"].update(mention.source_fields)
        row["mention_count"] += mention.count
        if len(merged) >= MAX_MENTIONS_PER_CACHE:
            break
    _absorb_unresolved(merged)
    return _finalize_rows(merged)


# ── v_relevance 三态投影(纯函数,读侧 / 回填共用) ────────────────────────────


def _v_relevance_inputs(row: dict[str, Any]) -> tuple[str, str, set[str], set[str]]:
    resolution = _text(row.get("resolution"))
    lens_key = _text(row.get("lens_key"))
    modalities = {m for m in (row.get("modalities") or []) if m in MODALITIES}
    sources = {item for item in (row.get("source_fields") or []) if item}
    return resolution, lens_key, modalities, sources


def v_relevance_for(row: dict[str, Any]) -> tuple[str, str]:
    """单条提及行 → (v_relevance, v_reason)。只看落表字段(resolution / lens_key / modalities /
    source_fields),回填统计与读侧端点共用同一口径;目录无该卡口退回家族级(note=mount_unmatched)
    仍按家族归一计。"""

    resolution, lens_key, modalities, sources = _v_relevance_inputs(row)
    if lens_key.startswith(SERIES_KEY_PREFIX):
        return "likely", "series_only"
    if resolution not in {"sku", "family"}:
        return "likely", "unresolved_mention"
    if sources and sources <= ADVISORY_FIELDS:
        return "likely", "advisory_field_only"
    if modalities & EXPLICIT_MODALITIES:
        return "confirmed", "catalog_match_explicit_modality"
    return "likely", "catalog_match_modality_unspecified"


def explain(result: Any, index: CatalogIndex) -> dict[str, Any]:
    """--cache-id 对照用的抽取轨迹:每个品牌锚点截到了什么、归一成什么。"""

    anchors: list[dict[str, Any]] = []
    for field_name, text in source_texts(result):
        for match in _BRAND_RE.finditer(text):
            window = text[match.end():match.end() + 120]
            body, series_codes = _clip(window)
            anchors.append({
                "field": field_name,
                "after": window[:60],
                "body": body,
                "series": series_codes,
                "pieces": split_slash_list(body) if body else [],
            })
            if len(anchors) >= 80:
                break
    rows = extract_resolved(result, index)
    return {
        "extractor_version": EXTRACTOR_VERSION,
        "alias_table_version": ALIAS_TABLE_VERSION,
        "anchors": anchors,
        "rows": [
            {k: row[k] for k in ("mention_text", "mention_norm", "resolution", "product_sku", "lens_key", "display_name", "candidate_skus", "modalities", "source_fields", "mention_count", "v_relevance", "v_reason", "note")}
            for row in rows
        ],
    }


__all__ = [
    "ADVISORY_FIELDS",
    "ALIAS_TABLE_VERSION",
    "EXTRACTOR_VERSION",
    "canonical_text",
    "FINAL_DERIVE_METHOD",
    "MODALITIES",
    "RESOLUTIONS",
    "SERIES_KEY_PREFIX",
    "V_RELEVANCE",
    "V_RELEVANCE_LABELS",
    "CatalogIndex",
    "detect_modalities",
    "explain",
    "extract_mentions",
    "extract_resolved",
    "family_name",
    "load_catalog_index",
    "normalize_mention",
    "parse_spec",
    "source_texts",
    "split_slash_list",
    "v_relevance_for",
]
