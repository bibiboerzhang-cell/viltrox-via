"""邮箱质检与跨源仲裁层 —— 让 outreach 永远拿到最优邮箱。

三件事(全部围绕 vkpi_kol_pool_contacts 的 email/business_email 行):
  ① 语法校验:严格 RFC 子集(小写化、local/domain 逐段校验、复用
     business_contact_extract._valid_email 的 TLD 白名单闸);拒占位域名
     (example.com/test.com 家族)与含控制字符/换行残端的腐蚀值;
     角色邮箱(noreply 家族)只标记不拒(role=True)。
  ② MX 校验:本地 DNS 查询。dnspython 在依赖里则用之;不在(当前仓库不在)
     走 subprocess dig 兜底;两者都不可用诚实记 mx_unknown。
     超时 2s 记 mx_unknown(不记 fail);NXDOMAIN 或「无 MX 且无 A」记 mx_bad;
     有 MX 记 mx_ok;无 MX 但有 A(RFC 5321 implicit MX)也记 mx_ok。
  ③ 跨源仲裁 best_email_for_kol():纯读、零网络,给 outreach 消费。

结果缓存落点(先查过表结构再定的决策):
  vkpi_kol_pool_contacts 没有任何 jsonb 列;verification_status 被 CHECK 约束
  锁死在 5 个枚举值(扩枚举=改 CHECK=DDL,本轮禁 DDL)。因此把校验结果写进
  既有 vkpi_kol_contact_evidence 表(FK 指向 contact 行,无需迁移):
  source_type='email_quality_check' + source_field 携带 verdict token,
  is_public_declared=false + 不在任何 verified 白名单里,已核实
  contact_ingest.set_contact_verification_status 与 contact_suppression 的
  evidence 读取都按 source_type 白名单 + is_public_declared=TRUE 过滤,
  本缓存行绝不会干扰既有 verified/observed 判定。

仲裁序(词典序,高优先在前):
  1. 硬坏垫底:语法失败 / 占位域名 / n 前缀腐蚀(值 = 'n'+同 KOL 另一邮箱)
     / mx_bad,永远排在任何可用候选之后(仍返回,供消费方兜底判断 usable)。
  2. 非角色邮箱 > 角色邮箱(noreply 家族对外联无用,只作最后手段)。
  3. 来源置信度:youtube_about_declared > website_declared(即任务所称
     website_scrape,落库真值是 website_declared)> raw_bio_scan > raw_full_scan。
  4. 校验状态:mx_ok > mx_unknown(同来源内 tiebreak;未校验≠更差来源)。
  5. 行内 confidence、last_seen 新鲜度、id。

红线:
  - DNS 查询也是出网:单查询超时 2s、并发 ≤4、单轮总预算 ≤200(硬夹)。
  - 明确不做 SMTP 探测(RCPT 验证是灰色行为,禁)。
  - best_email_for_kol 纯读零网络;MX 状态只认缓存,缓存缺席记 mx_unknown。
  - 不触 viltrox_fit_score / rule_v0;零 LLM;零 Apify。
  - 本模块不改 verification_status(那是 staff 门控的终态迁移,走
    contact_ingest.set_contact_verification_status);批报告只输出建议。
  - best_email_for_kol 返回含邮箱明文,仅供 outreach 域内消费;接 HTTP 前
    必须过 contact_access 门控,禁直接挂路由。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.business_contact_extract import _valid_email

logger = get_logger(__name__)

EVIDENCE_SOURCE_TYPE = "email_quality_check"
EMAIL_CONTACT_TYPES = ("email", "business_email")

# 占位域名:出现即拒(reason=placeholder_domain)。子域一并命中(如 mail.example.com)。
PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "test.com",
    # 2026-08-31 页面腿实测:文档/模板站的通用示例地址(example@domain.com)。
    "domain.com", "yourdomain.com", "email.com", "yoursite.com", "mysite.com",
})

# 平台自身域名:页面抓取时抓到的是平台客服/条款邮箱,不是这位 KOL 的联系方式。
# 2026-08-31 页面腿首批 50 个实测:18 个新邮箱里 3 个是这类(boosty.to/throne.com/
# patreon.com),按「不是本人邮箱」拒收,与占位域名同级。
PLATFORM_DOMAINS = frozenset({
    "patreon.com", "boosty.to", "throne.com", "ko-fi.com", "buymeacoffee.com",
    "linktr.ee", "beacons.ai", "stan.store", "gumroad.com", "substack.com",
    "shopify.com", "wixsite.com", "squarespace.com", "sellfy.com", "redbubble.com",
})

# 角色邮箱(noreply 家族):标记 role=True 但不拒 —— 仲裁时垫到非角色之后。
_ROLE_SQUASHED_PREFIXES = ("noreply", "donotreply")

# 来源置信度序(高=优)。website_scrape 是任务口径别名,落库真值 website_declared。
SOURCE_RANK: dict[str, int] = {
    "manual": 110,
    "youtube_about_declared": 100,
    "ig_business_profile": 95,
    "bio_explicit_contact": 90,
    "website_declared": 80,
    "website_scrape": 80,
    "video_caption": 55,
    "raw_bio_scan": 50,
    "raw_scan": 45,
    "raw_full_scan": 40,
}
DEFAULT_SOURCE_RANK = 30

MX_OK = "mx_ok"
MX_UNKNOWN = "mx_unknown"
MX_BAD = "mx_bad"
MX_RANK: dict[str, int] = {MX_OK: 2, MX_UNKNOWN: 1, MX_BAD: 0}

DNS_TIMEOUT_SECONDS = 2.0
MAX_DNS_CONCURRENCY = 4
MAX_DNS_BUDGET = 200  # 单轮红线,set_dns_budget 硬夹不放行更大值

_DNS_BUDGET = {"remaining": MAX_DNS_BUDGET}
_BUDGET_LOCK = threading.Lock()
_MX_CACHE: dict[str, tuple[str, str]] = {}
_MX_CACHE_LOCK = threading.Lock()

_CTRL_RE = re.compile(r"[\s\x00-\x1f\x7f]")
_LOCAL_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_DIG_STATUS_RE = re.compile(r"status:\s*([A-Z]+)")
_DIG_ANSWER_RE = re.compile(r"ANSWER:\s*(\d+)")


# ---------------------------------------------------------------- ① 语法校验
def _is_role_local(local: str) -> bool:
    squashed = re.sub(r"[._\-]", "", local)
    return squashed.startswith(_ROLE_SQUASHED_PREFIXES)


def _is_placeholder_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in PLACEHOLDER_DOMAINS)


def _is_platform_domain(domain: str) -> bool:
    """页面抓取抓到的平台客服/条款邮箱——不是这位 KOL 的联系方式,与占位域名同级拒收。"""
    return any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)


def _local_ok(local: str) -> bool:
    return 0 < len(local) <= 64 and bool(_LOCAL_RE.match(local))


def _domain_ok(domain: str) -> bool:
    if not domain or len(domain) > 253:
        return False
    return bool(_DOMAIN_RE.match(domain))


def _has_control_sequence(cleaned: str) -> bool:
    """换行腐蚀残端 / 控制字符 / 内嵌空白:一律畸形。"""
    return "\\n" in cleaned or "\\r" in cleaned or bool(_CTRL_RE.search(cleaned))


def _syntax_reason(value: str) -> tuple[str, str]:
    """返回 (清洗后邮箱, 拒绝原因);原因空串=语法通过。"""
    cleaned = (value or "").strip().rstrip(".").lower()
    if not cleaned:
        return "", "empty_value"
    if _has_control_sequence(cleaned):
        return cleaned, "control_or_whitespace"
    if cleaned.count("@") != 1:
        return cleaned, "at_sign_count"
    local, _, domain = cleaned.partition("@")
    if not _local_ok(local):
        return cleaned, "bad_local_part"
    if local == "n":
        # 换行腐蚀/@提及假命中签名:"\n@handle" 被 raw 扫描粘成 local='n'
        return cleaned, "n_mention_artifact"
    if not _domain_ok(domain):
        return cleaned, "bad_domain"
    if _is_placeholder_domain(domain):
        return cleaned, "placeholder_domain"
    if _is_platform_domain(domain):
        return cleaned, "platform_domain"
    if not _valid_email(cleaned):
        # 复用既有闸:占位邮箱名单 / CDN 后缀 / 假 TLD(@提及式假命中)
        return cleaned, "bad_tld_or_placeholder"
    return cleaned, ""


def validate_email_syntax(value: str) -> dict[str, Any]:
    """严格 RFC 子集语法校验。角色邮箱只标记不拒(ok 仍可为 True)。"""
    cleaned, reason = _syntax_reason(value)
    local, _, domain = cleaned.partition("@")
    return {
        "email": cleaned,
        "ok": reason == "",
        "reason": reason,
        "role": bool(local) and _is_role_local(local),
        "local": local,
        "domain": domain,
    }


def looks_n_prefix_corrupt(email: str, sibling_emails: Any) -> bool:
    """换行腐蚀签名:值 = 'n' + 同 KOL 另一条已存在邮箱(\\n 粘连产物)。"""
    e = (email or "").strip().lower()
    if len(e) < 3 or not e.startswith("n"):
        return False
    rest = e[1:]
    return rest != e and rest in {str(s or "").strip().lower() for s in sibling_emails}


# ---------------------------------------------------------------- ② MX 校验
def set_dns_budget(n: int) -> int:
    """设置本轮剩余 DNS 查询预算;硬夹在 [0, MAX_DNS_BUDGET],返回生效值。"""
    clamped = max(0, min(int(n), MAX_DNS_BUDGET))
    with _BUDGET_LOCK:
        _DNS_BUDGET["remaining"] = clamped
    if clamped != int(n):
        logger.warning("dns budget clamped: requested=%s effective=%s", n, clamped)
    return clamped


def dns_budget_remaining() -> int:
    with _BUDGET_LOCK:
        return int(_DNS_BUDGET["remaining"])


def _budget_take() -> bool:
    with _BUDGET_LOCK:
        if _DNS_BUDGET["remaining"] <= 0:
            return False
        _DNS_BUDGET["remaining"] -= 1
        return True


def clear_mx_cache() -> None:
    with _MX_CACHE_LOCK:
        _MX_CACHE.clear()


def _mx_via_dnspython(domain: str, timeout: float) -> tuple[str, str] | None:
    """dnspython 路径;库缺席返回 None(走 dig 兜底)。"""
    try:
        import dns.exception  # type: ignore[import-not-found]
        import dns.resolver  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=timeout)
        return (MX_OK, f"mx_records:{len(answers)}")
    except dns.resolver.NXDOMAIN:
        return (MX_BAD, "nxdomain")
    except dns.resolver.NoAnswer:
        return _implicit_a_fallback(domain, timeout)
    except (dns.exception.Timeout, dns.resolver.NoNameservers) as exc:
        return (MX_UNKNOWN, f"dns_soft_fail:{type(exc).__name__}")
    except dns.exception.DNSException as exc:
        logger.warning("dnspython mx query failed domain=%s: %s", domain, type(exc).__name__)
        return (MX_UNKNOWN, f"dns_error:{type(exc).__name__}")


def _run_dig(args: list[str], timeout: float) -> tuple[str, str]:
    """跑 dig,返回 (stdout, 错误标记);错误标记非空=本次查询作废(unknown)。"""
    try:
        proc = subprocess.run(  # noqa: S603 (本地 dig,参数受控)
            ["dig", "+time=2", "+tries=1", *args],
            capture_output=True,
            text=True,
            timeout=timeout + 1.0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", "dig_timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("dig unavailable/failed: %s", type(exc).__name__)
        return "", f"dig_error:{type(exc).__name__}"
    return proc.stdout or "", ""


def _implicit_a_fallback(domain: str, timeout: float) -> tuple[str, str]:
    """无 MX 时按 RFC 5321 implicit MX 查 A 记录(消耗第二次预算)。"""
    if not _budget_take():
        return (MX_UNKNOWN, "dns_budget_exhausted")
    stdout, err = _run_dig(["+short", "A", domain], timeout)
    if err:
        return (MX_UNKNOWN, err)
    if stdout.strip():
        return (MX_OK, "implicit_mx_a")
    return (MX_BAD, "no_mx_no_a")


def _mx_via_dig(domain: str, timeout: float) -> tuple[str, str]:
    stdout, err = _run_dig(["MX", domain], timeout)
    if err:
        return (MX_UNKNOWN, err)
    status_match = _DIG_STATUS_RE.search(stdout)
    status = status_match.group(1) if status_match else ""
    if status == "NXDOMAIN":
        return (MX_BAD, "nxdomain")
    if status != "NOERROR":
        return (MX_UNKNOWN, f"dns_status:{status or 'unparsed'}")
    answer_match = _DIG_ANSWER_RE.search(stdout)
    if answer_match and int(answer_match.group(1)) > 0:
        return (MX_OK, f"mx_records:{answer_match.group(1)}")
    return _implicit_a_fallback(domain, timeout)


def check_mx(domain: str, *, timeout: float = DNS_TIMEOUT_SECONDS, use_cache: bool = True) -> tuple[str, str]:
    """域名 MX 校验:(status, detail)。超时/无工具/预算耗尽一律 mx_unknown。"""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return (MX_BAD, "no_domain")
    if use_cache:
        with _MX_CACHE_LOCK:
            hit = _MX_CACHE.get(domain)
        if hit is not None:
            return hit
    if not _budget_take():
        return (MX_UNKNOWN, "dns_budget_exhausted")
    result = _mx_via_dnspython(domain, timeout)
    if result is None:
        result = _mx_via_dig(domain, timeout)
    with _MX_CACHE_LOCK:
        _MX_CACHE[domain] = result
    return result


# ---------------------------------------------------------------- 缓存读写
def _quality_fingerprint(email: str) -> str:
    return hashlib.sha256(f"email_quality:{(email or '').strip().lower()}".encode("utf-8")).hexdigest()


def _verdict_confidence(mx_status: str, syntax_ok: bool) -> float:
    if not syntax_ok or mx_status == MX_BAD:
        return 0.0
    return 1.0 if mx_status == MX_OK else 0.5


def encode_verdict(*, syntax_ok: bool, syntax_reason: str, mx_status: str, role: bool, detail: str) -> str:
    reason = syntax_reason if not syntax_ok else "ok"
    return f"syntax={reason};mx={mx_status};role={1 if role else 0};detail={detail}"[:200]


def parse_verdict(source_field: str) -> dict[str, Any]:
    tokens: dict[str, str] = {}
    for part in str(source_field or "").split(";"):
        key, sep, val = part.partition("=")
        if sep:
            tokens[key.strip()] = val.strip()
    syntax = tokens.get("syntax", "")
    return {
        "syntax_ok": syntax == "ok",
        "syntax_reason": "" if syntax == "ok" else syntax,
        "mx_status": tokens.get("mx", MX_UNKNOWN) or MX_UNKNOWN,
        "role": tokens.get("role") == "1",
        "detail": tokens.get("detail", ""),
    }


def cache_quality_result(
    conn: Any,
    *,
    contact_id: int,
    kol_pool_id: int,
    email: str,
    syntax_ok: bool,
    syntax_reason: str,
    mx_status: str,
    role: bool,
    detail: str = "",
    now: str | None = None,
) -> str:
    """把 verdict 写进 vkpi_kol_contact_evidence(upsert 同指纹行);不 commit。

    is_public_declared=false + source_type 不在任何 verified 白名单 → 不可能
    干扰既有 verified/observed 判定(已核对两处 evidence 读取端)。
    """
    at = now or datetime.now(timezone.utc).isoformat()
    fingerprint = _quality_fingerprint(email)
    field = encode_verdict(
        syntax_ok=syntax_ok, syntax_reason=syntax_reason, mx_status=mx_status, role=role, detail=detail
    )
    confidence = _verdict_confidence(mx_status, syntax_ok)
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_contact_evidence WHERE contact_id=? AND evidence_fingerprint=?",
        (int(contact_id), fingerprint),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE vkpi_kol_contact_evidence SET source_field=?, confidence=?, last_seen_at=? WHERE id=?",
            (field, confidence, at, int(dict(existing)["id"])),
        )
        return fingerprint
    conn.execute(
        """
        INSERT INTO vkpi_kol_contact_evidence
            (contact_id, kol_pool_id, source_type, source_url, source_field,
             evidence_fingerprint, confidence, is_public_declared, consent_basis,
             provider_run_ref, first_seen_at, last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(contact_id), int(kol_pool_id), EVIDENCE_SOURCE_TYPE, "", field,
            fingerprint, confidence, False, "source_observation", "", at, at,
        ),
    )
    return fingerprint


def load_quality_cache(conn: Any, contact_ids: list[int]) -> dict[int, dict[str, Any]]:
    """读取每个 contact 最新的质检 verdict;无缓存的 id 不在返回里。"""
    ids = [int(i) for i in contact_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT contact_id, source_field, last_seen_at, id
        FROM vkpi_kol_contact_evidence
        WHERE source_type=? AND contact_id IN ({placeholders})
        ORDER BY contact_id, last_seen_at, id
        """,
        (EVIDENCE_SOURCE_TYPE, *ids),
    ).fetchall()
    out: dict[int, dict[str, Any]] = {}
    for raw in rows:  # 排序升序,后写覆盖前写 → 留下每个 contact 最新一条
        row = dict(raw)
        verdict = parse_verdict(str(row.get("source_field") or ""))
        verdict["checked_at"] = str(row.get("last_seen_at") or "")
        out[int(row["contact_id"])] = verdict
    return out


# ---------------------------------------------------------------- ③ 跨源仲裁
def _email_rows_for_kol(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in EMAIL_CONTACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT id, contact_value, contact_source, confidence, verification_status,
               last_seen_at, created_at
        FROM vkpi_kol_pool_contacts
        WHERE kol_pool_id=? AND contact_type IN ({placeholders})
          AND COALESCE(contact_value,'')<>''
          AND verification_status NOT IN ('revoked','invalid')
        ORDER BY id
        """,
        (int(kol_pool_id), *EMAIL_CONTACT_TYPES),
    ).fetchall()
    return [dict(r) for r in rows]


def _bad_reason(syntax: dict[str, Any], corrupt: bool, mx_status: str) -> str:
    if syntax["reason"]:
        return str(syntax["reason"])
    if corrupt:
        return "n_prefix_corrupt"
    return "mx_bad" if mx_status == MX_BAD else ""


def _row_confidence(row: dict[str, Any], contact_id: int) -> float:
    try:
        return float(row.get("confidence") or 0.0)
    except (TypeError, ValueError):
        logger.warning("contact %s confidence 非数值,按 0 计", contact_id)
        return 0.0


def _build_candidate(row: dict[str, Any], cache: dict[int, dict[str, Any]], siblings: set[str]) -> dict[str, Any]:
    contact_id = int(row["id"])
    syntax = validate_email_syntax(str(row.get("contact_value") or ""))
    cached = cache.get(contact_id)
    mx_status = str(cached["mx_status"]) if cached else MX_UNKNOWN
    corrupt = looks_n_prefix_corrupt(syntax["email"], siblings - {syntax["email"]})
    hard_bad = (not syntax["ok"]) or corrupt or mx_status == MX_BAD
    reason = _bad_reason(syntax, corrupt, mx_status)
    row_confidence = _row_confidence(row, contact_id)
    return {
        "contact_id": contact_id,
        "email": syntax["email"],
        "contact_source": str(row.get("contact_source") or ""),
        "syntax_ok": bool(syntax["ok"]),
        "role": bool(syntax["role"]),
        "mx_status": mx_status,
        "mx_checked": cached is not None,
        "hard_bad": hard_bad,
        "bad_reason": reason,
        "source_rank": SOURCE_RANK.get(str(row.get("contact_source") or ""), DEFAULT_SOURCE_RANK),
        "row_confidence": row_confidence,
        "recency": str(row.get("last_seen_at") or row.get("created_at") or ""),
    }


def _rank_key(cand: dict[str, Any]) -> tuple:
    return (
        0 if cand["hard_bad"] else 1,
        0 if cand["role"] else 1,
        cand["source_rank"],
        MX_RANK.get(cand["mx_status"], 1),
        cand["row_confidence"],
        cand["recency"],
        cand["contact_id"],
    )


def best_email_for_kol(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """跨源仲裁最优邮箱(纯读、零网络、零成本),供 outreach 消费。

    revoked/invalid 终态行直接排除(合规);其余候选按模块头部仲裁序取最优。
    usable=False 表示最优候选也不该发信(语法坏/占位/腐蚀/mx_bad)。
    """
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()
    rows = _email_rows_for_kol(conn, int(kol_pool_id))
    if not rows:
        return {
            "kol_pool_id": int(kol_pool_id),
            "email": None,
            "reason": "no_email_rows",
            "candidates_considered": 0,
        }
    cache = load_quality_cache(conn, [int(r["id"]) for r in rows])
    siblings = {str(r.get("contact_value") or "").strip().lower() for r in rows}
    candidates = [_build_candidate(r, cache, siblings) for r in rows]
    best = max(candidates, key=_rank_key)
    return {
        "kol_pool_id": int(kol_pool_id),
        "email": best["email"],
        "contact_id": best["contact_id"],
        "contact_source": best["contact_source"],
        "mx_status": best["mx_status"],
        "mx_checked": best["mx_checked"],
        "syntax_ok": best["syntax_ok"],
        "role": best["role"],
        "usable": not best["hard_bad"],
        "reason": best["bad_reason"],
        "candidates_considered": len(candidates),
    }


# ---------------------------------------------------------------- 批校验
def select_unchecked_email_rows(conn: Any, limit: int) -> list[dict[str, Any]]:
    """默认口径:email 非空、非终态、且还没有质检缓存行。"""
    placeholders = ",".join("?" for _ in EMAIL_CONTACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT c.id, c.kol_pool_id, c.contact_value, c.contact_source
        FROM vkpi_kol_pool_contacts c
        WHERE c.contact_type IN ({placeholders})
          AND COALESCE(c.contact_value,'')<>''
          AND c.verification_status NOT IN ('revoked','invalid')
          AND NOT EXISTS (
              SELECT 1 FROM vkpi_kol_contact_evidence e
              WHERE e.contact_id = c.id AND e.source_type = ?
          )
        ORDER BY c.id
        LIMIT ?
        """,
        (*EMAIL_CONTACT_TYPES, EVIDENCE_SOURCE_TYPE, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _resolve_domains(domains: list[str], *, timeout: float) -> dict[str, tuple[str, str]]:
    """唯一域名并发 MX(并发硬夹 ≤MAX_DNS_CONCURRENCY;预算在 check_mx 内扣)。"""
    if not domains:
        return {}
    workers = max(1, min(MAX_DNS_CONCURRENCY, len(domains)))
    results: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for domain, result in zip(domains, pool.map(lambda d: check_mx(d, timeout=timeout), domains)):
            results[domain] = result
    return results


def _batch_verdict_for_row(
    row: dict[str, Any], mx_results: dict[str, tuple[str, str]], *, do_mx: bool
) -> dict[str, Any]:
    syntax = validate_email_syntax(str(row.get("contact_value") or ""))
    if not syntax["ok"]:
        return {**syntax, "mx_status": MX_UNKNOWN, "detail": "syntax_rejected"}
    if not do_mx:
        return {**syntax, "mx_status": MX_UNKNOWN, "detail": "mx_disabled"}
    status, detail = mx_results.get(syntax["domain"], (MX_UNKNOWN, "not_resolved"))
    return {**syntax, "mx_status": status, "detail": detail}


def _domains_to_resolve(rows: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for row in rows:
        syntax = validate_email_syntax(str(row.get("contact_value") or ""))
        if syntax["ok"] and syntax["domain"] not in seen:
            seen.add(syntax["domain"])
            domains.append(syntax["domain"])
    return domains


def _tally_verdict(state: dict[str, Any], row: dict[str, Any], verdict: dict[str, Any]) -> None:
    source = str(row.get("contact_source") or "")
    state["by_source"][source] = state["by_source"].get(source, 0) + 1
    if verdict["role"]:
        state["dist"]["role_flagged"] += 1
    if not verdict["ok"]:
        state["dist"]["syntax_bad"] += 1
        reason = str(verdict["reason"])
        state["by_reason"][reason] = state["by_reason"].get(reason, 0) + 1
    else:
        state["dist"][str(verdict["mx_status"])] += 1
    if (not verdict["ok"]) or verdict["mx_status"] == MX_BAD:
        state["invalid_suggestions"].append(
            {
                "contact_id": int(row["id"]),
                "domain": str(verdict.get("domain") or ""),
                "reason": str(verdict["reason"] or verdict["detail"]),
            }
        )


def run_batch(
    *,
    limit: int = 50,
    do_mx: bool = True,
    dns_budget: int | None = None,
    conn: Any | None = None,
    timeout: float = DNS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """批校验:选未校验行 → 语法 →(可选)MX → 写缓存 → 返回分布报告。

    报告只带 contact_id/域名/verdict,不铺邮箱明文。verification_status 不动;
    对确定坏行输出建议(交 staff 走 set_contact_verification_status)。
    """
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()
    budget_effective = set_dns_budget(dns_budget) if dns_budget is not None else dns_budget_remaining()
    rows = select_unchecked_email_rows(conn, int(limit))
    domains = _domains_to_resolve(rows) if do_mx else []
    mx_results = _resolve_domains(domains, timeout=timeout) if do_mx else {}
    state: dict[str, Any] = {
        "dist": {MX_OK: 0, MX_UNKNOWN: 0, MX_BAD: 0, "syntax_bad": 0, "role_flagged": 0},
        "by_source": {},
        "by_reason": {},
        "invalid_suggestions": [],
    }
    for row in rows:
        verdict = _batch_verdict_for_row(row, mx_results, do_mx=do_mx)
        cache_quality_result(
            conn,
            contact_id=int(row["id"]),
            kol_pool_id=int(row["kol_pool_id"]),
            email=str(row.get("contact_value") or ""),
            syntax_ok=bool(verdict["ok"]),
            syntax_reason=str(verdict["reason"]),
            mx_status=str(verdict["mx_status"]),
            role=bool(verdict["role"]),
            detail=str(verdict["detail"]),
        )
        _tally_verdict(state, row, verdict)
    conn.commit()
    return {
        "checked_rows": len(rows),
        "distribution": state["dist"],
        "by_source": state["by_source"],
        "syntax_bad_reasons": state["by_reason"],
        "unique_domains_resolved": len(domains),
        "dns_budget_effective": budget_effective,
        "dns_budget_remaining": dns_budget_remaining(),
        "invalid_suggestions": state["invalid_suggestions"][:20],
        "note": "verification_status 未改动;确定坏行请 staff 走 set_contact_verification_status('invalid')",
    }


# ---------------------------------------------------------------- CLI
def _emit(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="KOL 邮箱质检批校验(默认只跑未校验行)")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--no-mx", action="store_true", help="只跑语法层,零 DNS")
    parser.add_argument("--dns-budget", type=int, default=MAX_DNS_BUDGET, help=f"本轮 DNS 查询上限(硬夹 ≤{MAX_DNS_BUDGET})")
    args = parser.parse_args(argv)
    summary = run_batch(limit=args.limit, do_mx=not args.no_mx, dns_budget=args.dns_budget)
    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
