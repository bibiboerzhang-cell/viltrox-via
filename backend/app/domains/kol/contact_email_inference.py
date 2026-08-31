"""邮箱模式推断层 —— 对「有个人域名外链、但无邮箱」的 KOL 按行业惯例组合猜测。

**这一层产出的是线索,不是事实。** 行业里所谓 60-80% 邮箱覆盖率,大头就是这种
pattern guess;本模块把它做出来,但拒绝把它伪装成实抓结果。三条硬边界:

  ① MX 存在是**必要条件**,不是充分条件。域名收得了信 ≠ 这个地址存在。
     所以只有 mx_ok 的域名才出候选;mx_unknown 一律不出(不确定之上不再猜)。
  ② 推断结果**永远单独标记**:contact_source='pattern_inferred'、
     confidence=0.35、is_inferred=True、usable=False(绝不进自动发信)。
  ③ 仲裁 best_email_with_inference() 里,任何实抓邮箱**先于**全部推断候选;
     只有实抓侧空/不可用时才落到推断。域名上只要已有任一实抓邮箱,直接不推断。

明确不做:SMTP RCPT 探测(灰色行为,禁)。因此没有任何「地址真存在」的证据,
usable 恒 False —— 这是诚实口径,不是保守。

**为什么本模块不落库(核实过 schema 才下的结论,不是偷懒):**
  vkpi_kol_pool_contacts 现有 schema 没有能容纳「推断值」的落点 ——
  - contact_system._normalize_channel 把任何含 "email" 的 contact_type 归并进
    email 渠道(权重 55),refresh_contact_system_columns 会据此重算并写回
    contactability_score;落 'email_inferred' 会让「猜的」直接冒充「能邮件触达」。
  - 换个不含 email 的 contact_type 也仍吃 DEFAULT_CHANNEL_WEIGHT=3.0,照样污染分。
  - verification_status 被 CHECK 锁死在 5 个枚举值,没有一个表示「inferred」;
    扩枚举 = DDL,本轮禁 DDL。
  - vkpi_kol_contact_evidence 的 FK 指向 contact 行,没有 contact 行就写不进去。
  结论:先给正确的落点(迁移加 inferred 终态或独立表),再谈落库。本轮只产出
  build_persist_plan() —— 把「将来要写的行」原样吐出来,供下一刀直接消费。

红线:零 Apify、零 LLM;DNS 走 contact_email_quality 的 MX 层与共享预算
(该层硬夹 MAX_DNS_BUDGET=200/轮,本模块自身再声明 ≤500,取两者更小);
不触 viltrox_fit_score / rule_v0;不打印密钥。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.domains.kol import contact_email_quality as ceq

logger = get_logger(__name__)

PATTERN_INFERRED_SOURCE = "pattern_inferred"
PATTERN_INFERRED_CONFIDENCE = 0.35
MAX_CANDIDATES_PER_DOMAIN = 12
# 本模块自身声明的单轮 DNS 上限;实际生效值再被 ceq.MAX_DNS_BUDGET 夹一次。
MAX_INFERENCE_DNS_BUDGET = 500

# 角色 local(行业惯例,按命中率经验序;前 9 个 + 最多 3 个人名派生 = 12 上限)。
ROLE_LOCALS: tuple[str, ...] = (
    "hello", "contact", "info", "hi", "mail",
    "booking", "business", "inquiries", "press",
)

# ceq.PLATFORM_DOMAINS 覆盖不到的「不是个人域名」的域:短链、聚合页、社媒、
# 市场、托管商店、newsletter 服务。这些域上的邮箱要么不存在,要么不是本人的。
# 不改 ceq 的名单(那是别人的文件),在这里做增量并集。
EXTRA_NON_PERSONAL_DOMAINS = frozenset({
    # 短链 / 跳转
    "bit.ly", "amzn.to", "t.co", "goo.gl", "tinyurl.com", "rb.gy", "cutt.ly",
    "lnk.bio", "linkin.bio", "shorturl.at", "s.click.aliexpress.com",
    # 支付 / 打赏 / 众筹
    "paypal.me", "venmo.com", "cash.app", "kickstarter.com", "indiegogo.com",
    "gofundme.com", "tipeee.com", "liberapay.com",
    # 聚合页 / 名片页(ceq 已含 linktr.ee/beacons.ai/stan.store)
    "milkshake.app", "carrd.co", "bio.link", "campsite.bio", "solo.to",
    "linkpop.com", "taplink.cc", "koji.to", "allmylinks.com",
    # 社媒 / UGC 平台(ceq 已含主流几家)
    "x.com", "twitter.com", "threads.com", "threads.net", "reddit.com",
    "pinterest.com", "linkedin.com", "vimeo.com", "twitch.tv", "discord.gg",
    "flickr.com", "500px.com", "behance.net", "dribbble.com", "deviantart.com",
    "tumblr.com", "medium.com", "note.com", "bilibili.com", "weibo.com",
    "xiaohongshu.com", "douyin.com", "kuaishou.com", "telegram.me", "t.me",
    "vk.com", "snapchat.com", "whatsapp.com", "wa.me",
    # 托管商店 / 素材市场 / 课程平台
    "etsy.com", "ebay.com", "aliexpress.com", "bigcartel.com", "stores.jp",
    "booth.pm", "creativemarket.com", "artstation.com", "society6.com",
    "teespring.com", "spring.by.me", "printful.com", "teachable.com",
    "udemy.com", "skillshare.com", "kit.co", "audiio.com", "epidemicsound.com",
    "artlist.io", "musicbed.com", "soundstripe.com",
    # newsletter / 表单 / 日程
    "sendfox.com", "convertkit.com", "mailerlite.com", "beehiiv.com",
    "calendly.com", "typeform.com", "forms.gle", "docs.google.com",
    "notion.so", "notion.site", "airtable.com",
    # 免费邮箱提供商(外链里极少见,但出现即不是个人域名)
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "qq.com", "163.com", "126.com",
    "naver.com", "protonmail.com", "proton.me", "gmx.com", "web.de",
    # 2026-08-31 首批 60 域实测漏网:共享作品集托管 / 联盟短链 / 刊物 / 播客托管
    "myportfolio.com", "amzlink.to", "anchor.fm", "fotocommunity.de",
    "fstoppers.com", "logi.link", "smugmug.com", "zenfolio.com", "pixieset.com",
    "format.com", "cargo.site", "adobe.io",
    # 第二遍 60 域实测漏网:社媒新站 + 联盟/带货短链(器材类 KOL 简介里高频)
    "bsky.app", "bsky.social", "spoti.fi", "shortads.io", "geni.us",
    "liketk.it", "rstyle.me", "shopmy.us", "shrsl.com", "avantlink.com",
    "howl.link", "imp.i", "ltk.app",
})

# 品牌词干闸:平台常有多国别域名(pinterest.co.uk / facebook.de / fb.com),
# 逐个枚举域名永远追不上。改比对可注册域的**首段词干**,一刀覆盖全部 ccTLD 变体。
# 只放明确的平台/品牌词,不放通用英文词(通用词会误伤个人域名)。
NON_PERSONAL_STEMS = frozenset({
    "facebook", "fb", "instagram", "twitter", "pinterest", "linkedin",
    "youtube", "tiktok", "snapchat", "whatsapp", "telegram", "discord",
    "spotify", "soundcloud", "anchor", "apple", "google", "microsoft",
    "adobe", "dropbox", "github", "gitlab", "reddit", "tumblr", "medium",
    "vimeo", "twitch", "amazon", "amzn", "amzlink", "ebay", "etsy",
    "aliexpress", "paypal", "venmo", "patreon", "linktree", "linktr",
    "beacons", "kickstarter", "gofundme", "substack", "wordpress", "wix",
    "squarespace", "shopify", "myportfolio", "smugmug", "zenfolio",
    "pixieset", "behance", "dribbble", "deviantart", "flickr", "500px",
    "artstation", "fotocommunity", "fstoppers", "notion", "calendly",
    "mailchimp", "weibo", "bilibili", "douyin", "xiaohongshu", "zhihu",
    "kuaishou", "naver", "kakao", "yandex", "logi",
    "bsky", "spoti", "geni", "shortads", "liketk", "rstyle", "shopmy",
    "shrsl", "avantlink",
})
# 注意:零售商域名(parkcameras.com / bhphotovideo.com)**不排除** —— 池里有品牌
# 频道,对它们来说零售商域就是自己的邮箱域。排除口径是「不是这位 KOL 的域」,
# 不是「不是个人的域」。

# 二级公共后缀(无 PSL 库时的最小可用集):命中则保留 3 段作为可注册域。
_MULTIPART_TLDS = frozenset({
    "co.uk", "org.uk", "me.uk", "ac.uk", "co.jp", "ne.jp", "or.jp", "stores.jp",
    "com.au", "net.au", "org.au", "com.br", "com.tw", "com.cn", "net.cn",
    "org.cn", "com.hk", "com.sg", "com.mx", "com.tr", "co.kr", "co.nz",
    "co.za", "co.in", "com.ar", "com.es", "co.il", "com.ua", "com.pl",
})

# 常见前缀子域:剥掉它取主域(mail.x.com / shop.x.com 的邮箱域都是 x.com)。
_STRIPPABLE_SUBDOMAINS = frozenset({
    "www", "www2", "m", "en", "de", "fr", "es", "it", "jp", "cn", "kr",
    "mail", "email", "shop", "store", "blog", "about", "contact", "portfolio",
    "info", "home", "web", "my", "app", "link", "links", "go",
})

# 人名派生时先剔掉的品牌/职业噪声词(仅在剔完仍有 ≥1 词时生效)。
_NOISE_NAME_TOKENS = frozenset({
    "official", "studio", "studios", "media", "films", "film", "photo",
    "photos", "photography", "photographer", "productions", "production",
    "tv", "channel", "creative", "creatives", "visuals", "visual", "art",
    "arts", "gear", "review", "reviews", "the", "hd", "4k", "vlog", "vlogs",
})

_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_NAME_SPLIT_RE = re.compile(r"[^a-z0-9]+")


# ------------------------------------------------------------------ 域名归一
def registrable_domain(url_or_host: str) -> str:
    """从外链取可注册域(小写、去 www/常见前缀子域);取不到返回空串。"""
    raw = str(url_or_host or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = (urlparse(raw).netloc or "").lower()
    except ValueError:
        logger.warning("外链 netloc 解析失败,跳过(值不入日志)")
        return ""
    host = host.split("@")[-1].split(":")[0].strip().rstrip(".")
    if not host or "." not in host or _IPV4_RE.match(host):
        return ""
    labels = host.split(".")
    while len(labels) > 2 and labels[0] in _STRIPPABLE_SUBDOMAINS:
        labels = labels[1:]
    if len(labels) > 2 and ".".join(labels[-2:]) in _MULTIPART_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) > 2 else ".".join(labels)


def _in_domain_set(domain: str, names: frozenset[str]) -> bool:
    return any(domain == n or domain.endswith("." + n) for n in names)


def is_personal_domain(domain: str) -> bool:
    """是否可作为「这位 KOL 自己的邮箱域」:排平台域、占位域、非个人域。"""
    d = str(domain or "").strip().lower()
    if not d or "." not in d:
        return False
    if _in_domain_set(d, ceq.PLATFORM_DOMAINS) or _in_domain_set(d, ceq.PLACEHOLDER_DOMAINS):
        return False
    if _in_domain_set(d, EXTRA_NON_PERSONAL_DOMAINS):
        return False
    if d.split(".")[0] in NON_PERSONAL_STEMS:  # 覆盖 pinterest.co.uk / facebook.de 类变体
        return False
    # 借 ceq 的语法闸再过一道(假 TLD / CDN 后缀 / 占位邮箱名单)。
    return bool(ceq.validate_email_syntax("hello@" + d)["ok"])


# ------------------------------------------------------------------ 人名派生
def _ascii_tokens(value: str) -> list[str]:
    """Unicode → ASCII → 小写词元;CJK 等无 ASCII 映射的名字会得到空列表。"""
    folded = unicodedata.normalize("NFKD", str(value or ""))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return [t for t in _NAME_SPLIT_RE.split(ascii_only) if t and not t.isdigit()]


def _denoise(tokens: list[str]) -> list[str]:
    cleaned = [t for t in tokens if t not in _NOISE_NAME_TOKENS and len(t) > 1]
    return cleaned or [t for t in tokens if len(t) > 1]


def derive_name_locals(handle: str, display_name: str) -> list[str]:
    """派生 first / firstname.lastname / f.lastname;确定性,按此固定序返回。

    优先 display_name(真名更可能是邮箱 local);它派生不出东西才退回 handle。
    """
    for source in (display_name, handle):
        tokens = _denoise(_ascii_tokens(str(source or "").lstrip("@")))
        if not tokens:
            continue
        first, last = tokens[0], tokens[-1]
        out = [first]
        if last != first:
            out.append(f"{first}.{last}")
            out.append(f"{first[0]}.{last}")
        return out
    return []


# ------------------------------------------------------------ 候选生成(纯函数)
def generate_candidates(
    *, domain: str, handle: str = "", display_name: str = ""
) -> list[dict[str, Any]]:
    """给单个域名生成候选地址(确定性、零网络、零成本);上限 12 个。

    序 = 角色 local(经验命中率序)在前,人名派生在后。每项自带
    pattern 标签(role_<local> / name_first / name_first_last / name_initial_last)
    与 contact_source='pattern_inferred' + confidence=0.35。
    """
    d = str(domain or "").strip().lower()
    if not is_personal_domain(d):
        return []
    name_locals = derive_name_locals(handle, display_name)
    name_patterns = ("name_first", "name_first_last", "name_initial_last")
    ordered: list[tuple[str, str]] = [(local, f"role_{local}") for local in ROLE_LOCALS]
    ordered += [(local, name_patterns[i]) for i, local in enumerate(name_locals)]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for local, pattern in ordered:
        email = f"{local}@{d}"
        if email in seen or not ceq.validate_email_syntax(email)["ok"]:
            continue
        seen.add(email)
        out.append({
            "email": email,
            "local": local,
            "domain": d,
            "pattern": pattern,
            "contact_source": PATTERN_INFERRED_SOURCE,
            "confidence": PATTERN_INFERRED_CONFIDENCE,
            "is_inferred": True,
            "usable": False,
        })
        if len(out) >= MAX_CANDIDATES_PER_DOMAIN:
            break
    return out


# ------------------------------------------------------------------ 库侧取数
def domains_with_real_email(conn: Any) -> set[str]:
    """已有任一**实抓**邮箱的域名集合 —— 这些域一律不再推断(有真的就不猜)。"""
    placeholders = ",".join("?" for _ in ceq.EMAIL_CONTACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT contact_value
        FROM vkpi_kol_pool_contacts
        WHERE contact_type IN ({placeholders})
          AND COALESCE(contact_value,'')<>''
          AND contact_source <> ?
        """,
        (*ceq.EMAIL_CONTACT_TYPES, PATTERN_INFERRED_SOURCE),
    ).fetchall()
    out: set[str] = set()
    for raw in rows:
        value = str(dict(raw).get("contact_value") or "")
        syntax = ceq.validate_email_syntax(value)
        if syntax["ok"] and syntax["domain"]:
            out.add(syntax["domain"])
            out.add(registrable_domain(syntax["domain"]))
    out.discard("")
    return out


def select_inference_targets(conn: Any, limit: int) -> list[dict[str, Any]]:
    """选「有外链、无任何实抓邮箱」的 KOL,按域名聚合(每域取首个 KOL 的身份)。

    limit 计的是**域名个数**(DNS 查询按域名走,预算也按域名算)。
    """
    placeholders = ",".join("?" for _ in ceq.EMAIL_CONTACT_TYPES)
    rows = conn.execute(
        f"""
        SELECT c.kol_pool_id AS kol_pool_id, c.contact_value AS link,
               p.platform AS platform, p.handle AS handle, p.display_name AS display_name
        FROM vkpi_kol_pool_contacts c
        JOIN vkpi_kol_pool p ON p.id = c.kol_pool_id
        WHERE c.contact_type IN ('website','link_hub')
          AND COALESCE(c.contact_value,'')<>''
          AND COALESCE(p.email,'')=''
          AND NOT EXISTS (
              SELECT 1 FROM vkpi_kol_pool_contacts e
              WHERE e.kol_pool_id = c.kol_pool_id
                AND e.contact_type IN ({placeholders})
                AND COALESCE(e.contact_value,'')<>''
          )
        ORDER BY c.kol_pool_id, c.id
        """,
        (*ceq.EMAIL_CONTACT_TYPES,),
    ).fetchall()
    taken = domains_with_real_email(conn)
    grouped: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        domain = registrable_domain(str(row.get("link") or ""))
        if not domain or domain in taken or domain in grouped:
            continue
        if not is_personal_domain(domain):
            continue
        grouped[domain] = {
            "domain": domain,
            "kol_pool_id": int(row["kol_pool_id"]),
            "platform": str(row.get("platform") or ""),
            "handle": str(row.get("handle") or ""),
            "display_name": str(row.get("display_name") or ""),
        }
        if len(grouped) >= int(limit):
            break
    return list(grouped.values())


# ------------------------------------------------------------------ MX 收敛
def resolve_effective_dns_budget(requested: int) -> int:
    """本模块 ≤500,再交给 ceq.set_dns_budget 夹一次(该层硬顶 200/轮)。"""
    asked = max(0, min(int(requested), MAX_INFERENCE_DNS_BUDGET))
    return ceq.set_dns_budget(asked)


def infer_for_targets(
    targets: list[dict[str, Any]],
    *,
    dns_budget: int = MAX_INFERENCE_DNS_BUDGET,
    timeout: float = ceq.DNS_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """对目标域名跑 MX 闸 → 只给 mx_ok 的域出候选。返回可直接汇报的结构。

    mx_unknown 不出候选(不确定之上不再猜),但单列出来供人工判断。
    """
    effective_budget = resolve_effective_dns_budget(dns_budget)
    results: list[dict[str, Any]] = []
    mx_dist: dict[str, int] = {ceq.MX_OK: 0, ceq.MX_UNKNOWN: 0, ceq.MX_BAD: 0}
    pattern_dist: dict[str, int] = {}
    for target in targets:
        domain = str(target["domain"])
        status, detail = ceq.check_mx(domain, timeout=timeout)
        mx_dist[status] = mx_dist.get(status, 0) + 1
        candidates = (
            generate_candidates(
                domain=domain,
                handle=str(target.get("handle") or ""),
                display_name=str(target.get("display_name") or ""),
            )
            if status == ceq.MX_OK
            else []
        )
        for cand in candidates:
            pattern_dist[cand["pattern"]] = pattern_dist.get(cand["pattern"], 0) + 1
        results.append({**target, "mx_status": status, "mx_detail": detail, "candidates": candidates})
    return {
        "domains_examined": len(targets),
        "mx_distribution": mx_dist,
        "domains_with_candidates": sum(1 for r in results if r["candidates"]),
        "candidates_total": sum(len(r["candidates"]) for r in results),
        "pattern_distribution": pattern_dist,
        "dns_budget_requested": int(dns_budget),
        "dns_budget_effective": effective_budget,
        "dns_budget_remaining": ceq.dns_budget_remaining(),
        "results": results,
    }


# ------------------------------------------------------------------ 仲裁(读)
def best_email_with_inference(
    kol_pool_id: int,
    *,
    conn: Any,
    inferred: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """仲裁:**任何实抓邮箱都排在全部推断候选之前**;实抓侧空/不可用才落到推断。

    返回里 is_inferred 标识来源性质;推断结果 usable 恒 False —— 没做 SMTP 探测,
    没有任何「地址存在」的证据,展示面必须据此区分,不许当实抓邮箱用。
    """
    real = ceq.best_email_for_kol(int(kol_pool_id), conn=conn)
    if real.get("email") and real.get("usable"):
        return {**real, "is_inferred": False}
    candidates = list(inferred or [])
    if not candidates:
        return {**real, "is_inferred": False}
    top = candidates[0]
    return {
        "kol_pool_id": int(kol_pool_id),
        "email": top["email"],
        "contact_source": PATTERN_INFERRED_SOURCE,
        "confidence": PATTERN_INFERRED_CONFIDENCE,
        "pattern": top["pattern"],
        "is_inferred": True,
        "usable": False,
        "reason": "pattern_inferred_lead_not_fact",
        "real_email_fallback": real.get("email"),
        "real_email_reason": real.get("reason", ""),
        "candidates_considered": len(candidates),
    }


# --------------------------------------------------------------- 落库预案(不写)
def build_persist_plan(result: dict[str, Any]) -> list[dict[str, Any]]:
    """把推断结果折成「将来要写的行」原样返回 —— 本轮**不执行任何写入**。

    落点为何被挡住,见模块头部。等迁移给出 inferred 终态/独立表后,下一刀直接
    消费这个结构即可,不必重跑 DNS。
    """
    plan: list[dict[str, Any]] = []
    for row in result.get("results", []):
        for cand in row.get("candidates", []):
            plan.append({
                "kol_pool_id": int(row["kol_pool_id"]),
                "contact_value": cand["email"],
                "contact_source": PATTERN_INFERRED_SOURCE,
                "confidence": PATTERN_INFERRED_CONFIDENCE,
                "is_public_declared": False,
                "pattern": cand["pattern"],
                "mx_status": row.get("mx_status", ceq.MX_UNKNOWN),
                "blocked_reason": "no_schema_landing_zone_for_inferred_email",
            })
    return plan
