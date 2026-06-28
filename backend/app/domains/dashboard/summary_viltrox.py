"""Dashboard summary Viltrox-only evidence filter (moved from summary.py, behavior unchanged)."""
from __future__ import annotations

from app.db.connection import table_exists


# --- KOL 榜单 Viltrox 过滤口径(本会话 2026-06-16)-------------------------------
# 「KOL 榜单」只收 KOL 发的 Viltrox 相关视频。判定优先用结构化深析信号
# (vkpi_kol_llm_deep_analysis_results.llm_dimensions_11,Gemini final_v1,layer1_summary
# 含 brand_exposure / product_presence / content_summary);无深析的视频用关键词兜底
# (口径同 app/core/constants.VILTROX_BRAND_KEYWORDS / app/services/audit/similarity 的 is_viltrox)。
#
# 关键坑(已取证):深析里 463/468 的 layer1 字段是「字符串」而非结构化对象,且常出现否定句
# ——例如 "None. No Viltrox logos..."、"无 Viltrox 露出"、"完全没有 Viltrox 品牌露出"。
# 纯子串匹配 "viltrox" 会把这些非 Viltrox 视频(Rick Astley/Sony/Fujifilm 评测等)误收。
# 因此深析判定 = 字段提到 Viltrox 关键词 且 该字段不是否定句(NOT match 否定正则)。
# 取证口径(2026-06-16 线上库):全 evidence 2631;468 有 ready video_final_v1;裸子串 455「命中」
# 里含 ~30 条否定句(如 Rick Astley/Fujifilm/Sony/Hasselblad 评测被误收);否定感知后 ~425 条真 Viltrox。
# 无深析 2163 条里仅 305 条 title 关键词命中(故绝不能纯关键词,会漏掉大量真 Viltrox 视频)。
#
# SQL 文本直接拼字面量(全是受控常量,无外部输入);% 需写成 %% —— get_conn().execute() 不传 params
# 时 psycopg 仍把裸 % 当占位符(只允许 %s/%b/%t)。正则用 Postgres ~*(大小写不敏感)。
_VILTROX_KEYWORDS_SQL = (
    "viltrox",       # 英文品牌
    "唯卓仕",        # 中文品牌
    "nexusfocus",    # 子品牌
    "nexus focus f1",
)
# 品牌关键词正则(Postgres ~*,大小写不敏感)。
_VILTROX_KW_REGEX = "viltrox|唯卓仕|nexusfocus"
# 否定「占位」正则:把「明确说没有 Viltrox」的片段从文本里抹掉,再看是否还有 Viltrox 关键词存活。
# 这样能同时处理:① 否定词在前(no/none/zero/未提及/无/没有/未见 ... viltrox);
# ② 否定在后(viltrox ... 为 0 / 0% / 零 / 无露出 / neither / not mentioned/visible)。
# 抹掉后仍有关键词存活 = 这条视频真有 Viltrox 的肯定提及。比纯子串稳:Rick Astley「None. No Viltrox…」
# / Fujifilm「Viltrox is neither mentioned nor visible」/「无 Viltrox 露出」等会被正确排除。
_VILTROX_NEG_OCCURRENCE_REGEX = (
    "(no|none|zero|not|neither|without|未提及|未见|未出现|没有|无|缺位|未|零)"
    "[^。.!?；;\\n]{0,14}(viltrox|唯卓仕)"
    "|(viltrox|唯卓仕)[^。.!?；;\\n]{0,14}"
    # 注意:正则里出现的字面 % 必须写成 %%(execute 把裸 % 当 psycopg 占位符)。
    "(为 0|0 %%|0%%|零|无露出|缺位|neither|not mentioned|not visible|not present|not featured)"
)


def _viltrox_text_like(column_expr: str) -> str:
    """SQL bool expr: TRUE when column_expr text mentions any Viltrox brand keyword (keyword-only)."""
    clauses = [
        f"lower({column_expr}) LIKE '%%{kw}%%'" if kw.isascii() else f"{column_expr} LIKE '%%{kw}%%'"
        for kw in _VILTROX_KEYWORDS_SQL
    ]
    return "(" + " OR ".join(clauses) + ")"


# 深析信号:把 layer1 的 brand_exposure / product_presence / content_summary 三段拼起来,
# 抹掉「明确否定 Viltrox」的片段后仍有品牌关键词存活,即判定该视频肯定提及 Viltrox。
# 字段缺失用 '' 兜底避免 NULL 短路;拼接用 ' || ' 隔开防止跨字段误连成否定窗口。
_DEEP_COMBINED = (
    "("
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'brand_exposure','')||' || '||"
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'product_presence','')||' || '||"
    "COALESCE(d.llm_dimensions_11->'layer1_summary'->>'content_summary','')"
    ")"
)
_VILTROX_DEEP_SIGNAL = (
    f"(regexp_replace({_DEEP_COMBINED}, '{_VILTROX_NEG_OCCURRENCE_REGEX}', ' ', 'gi') ~* '{_VILTROX_KW_REGEX}')"
)

# 关键词兜底(只对无深析的视频用):标题/视频标题/URL 命中品牌词。
_VILTROX_KW_FALLBACK = _viltrox_text_like(
    "(COALESCE(e.title,'')||' '||COALESCE(e.video_title,'')||' '||COALESCE(e.content_url,''))"
)

# 一条 evidence(别名 e)算 Viltrox 相关 = 深析肯定提到 Viltrox,或(无深析时)关键词兜底命中。
# 有深析但不提 / 明确否定 Viltrox 的视频会被排除(非 Viltrox 内容不进榜),这正是本次目的。
_VILTROX_EVIDENCE_PREDICATE = f"""(
    EXISTS (
        SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
        WHERE d.source_evidence_id = e.id
          AND d.analysis_kind = 'video_final_v1'
          AND d.status = 'ready'
          AND {_VILTROX_DEEP_SIGNAL}
    )
    OR (
        NOT EXISTS (
            SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
            WHERE d.source_evidence_id = e.id
              AND d.analysis_kind = 'video_final_v1'
              AND d.status = 'ready'
        )
        AND {_VILTROX_KW_FALLBACK}
    )
)"""


def _viltrox_evidence_predicate() -> str:
    """Viltrox-only filter on alias `e` (vkpi_kol_video_evidence).

    Degrades gracefully: if the deep-analysis table is missing, fall back to keyword-only on title/url.
    复用提示:MY KOL 簇要做同样过滤时,直接拼接本 predicate(要求 SQL 里有别名 e 指向
    vkpi_kol_video_evidence;或调 _viltrox_field_positive / _VILTROX_DEEP_SIGNAL 复用深析口径)。
    """
    if not table_exists("vkpi_kol_llm_deep_analysis_results"):
        return _VILTROX_KW_FALLBACK
    return _VILTROX_EVIDENCE_PREDICATE
