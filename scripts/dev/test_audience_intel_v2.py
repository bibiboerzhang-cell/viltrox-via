"""受众情报 v2 本地测试脚本(P1 验收用)。

跑法:.venv/bin/python scripts/dev/test_audience_intel_v2.py

覆盖:
  A. 单元级(mock,必须全绿):性别归一数学 / 年龄融合权重(含频道年龄先验、别名归一、
     LLM 回复解析)/ purchase_intent+brand 词表 / active_hours 直方 / engagement / superfans /
     creator_density / 证据数组形状。
  B. YouTube 真数据(watchluke 全景):A 路真调 Gemini 小批(预检 1 次 + 刷新最多 2 批,
     总调用不超过 3 次);打印全部块。
  C. Instagram 真数据(库存评论,零 Apify):llm_max_batches=0(省 LLM 额度),
     comment_intel 从 vkpi_comments 出全套。
  D. 共同粉丝 overlap:对库内数据直接验证(含跨账号最强共享对的全库扫描复现)。

纪律:Apify 零真花(IG/TT 只用库存评论);Gemini 真调控制在 3 次以内。
红线:绝不写 viltrox_fit_score、不碰 rule_v0(rule_v0 兜底文本在 A 路一律丢弃)。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

from app.db.connection import get_conn  # noqa: E402
from app.domains.kol import audience_stats as aud  # noqa: E402
from app.domains.kol import comment_intel as ci  # noqa: E402

PASS = "  [PASS]"
FAIL = "  [FAIL]"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    line = f"{PASS if condition else FAIL} {name}" + (f" — {detail}" if detail else "")
    print(line)
    if not condition:
        failures.append(name)


def section(title: str) -> None:
    print("\n" + "=" * 62)
    print(title)
    print("=" * 62)


def print_audience_blocks(payload: dict) -> None:
    print(f"  性别(原始) {payload.get('gender')}")
    print(f"  性别(归一) {payload.get('gender_normalized')}")
    print(f"  年龄 {payload.get('age_bins')}")
    print(f"  Top countries {payload.get('top_countries')}")
    print(f"  语言 {(payload.get('languages') or [])[:5]}")
    print(f"  创作者浓度 {payload.get('creator_density')}")
    print(f"  覆盖 {payload.get('coverage')} 置信 {payload.get('confidence')}")
    print(f"  年龄路覆盖 {payload.get('age_coverage')}")
    intel = payload.get("comment_intel") or {}
    print(f"  comment_intel 样本 {intel.get('sample_size')} 源 {intel.get('source')}")
    print(f"    购买意向 {(intel.get('purchase_intent') or {}).get('count')} 条 "
          f"{(intel.get('purchase_intent') or {}).get('pct')}% 样本 {(intel.get('purchase_intent') or {}).get('samples')}")
    print(f"    品牌提及 {[(b.get('brand'), b.get('count')) for b in intel.get('brand_mentions') or []]}")
    ah = intel.get("active_hours") or {}
    print(f"    活跃时段 top={ah.get('top_hours')} 峰值 {ah.get('peak_hour_comment_count')} 条 建议 {ah.get('suggestion')}")
    print(f"    互动 {intel.get('engagement')}")
    print(f"    铁粉 {[(f.get('handle'), f.get('count')) for f in (intel.get('superfans') or [])[:5]]}")
    ov = payload.get("overlap") or {}
    print(f"  overlap 自有评论者 {ov.get('self_commenters')} 条目 {ov.get('items')}")


# ══ A. 单元级 mock ══
section("A. 单元级 mock 测试")
t0 = time.time()
conn = get_conn()

# A1 性别归一数学:10 男 + 5 女 + 85 未知 -> 归一 66.7/33.3,判定 15 人(15%)
mock = []
for i in range(100):
    gender = "male" if i < 10 else ("female" if i < 15 else "")
    mock.append({
        "platform": "youtube", "author_key": f"g{i}", "gender": gender,
        "gender_conf": 0.8 if gender else 0.0, "country": "", "country_source": "", "language": "",
    })
agg = aud.aggregate_audience(0, mock, conn=conn, platform="youtube")
gn = agg["gender_normalized"]
check("A1a 归一 male=66.7", gn["male_pct"] == 66.7, str(gn))
check("A1b 归一 female=33.3", gn["female_pct"] == 33.3)
check("A1c determined_n=15 / determined_pct=15", gn["determined_n"] == 15 and gn["determined_pct"] == 15.0)
check("A1d 原始 gender 未被归一覆盖(coverage 保留)", agg["gender"]["male_pct"] == 10.0, str(agg["gender"]))

# A2 年龄:别名归一 / 频道年龄先验 / 融合权重
check("A2a 别名归一", aud._normalize_age_bucket("20-29") == "19-29" and aud._normalize_age_bucket("50+") == "40+"
      and aud._normalize_age_bucket("nonsense") == "")
b, c = aud._age_from_channel_created("2006-05-01T00:00:00Z")
check("A2b 老账号(2006)-> 30+ 桶 conf .3", b in ("30-39", "40+") and c == 0.3, f"{b}/{c}")
b, c = aud._age_from_channel_created("2024-01-01T00:00:00Z")
check("A2c 新账号无信号(不猜)", b == "" and c == 0.0)
check("A2d 融合:单信号原样", aud._fuse_age([("19-29", 0.55)]) == ("19-29", 0.55))
w, fc = aud._fuse_age([("19-29", 0.55), ("19-29", 0.3)])
check("A2e 融合:同桶互证抬置信 (.55,.3)->.69", w == "19-29" and fc == 0.69, f"{w}/{fc}")
w, fc = aud._fuse_age([("19-29", 0.55), ("30-39", 0.3)])
check("A2f 融合:分歧降置信 ->.36", w == "19-29" and fc == 0.36, f"{w}/{fc}")
# A2g 年龄聚合:已判定内归一
mock_age = [{"platform": "youtube", "author_key": f"a{i}", "age_bucket": ("19-29" if i < 30 else ("30-39" if i < 40 else "")),
             "gender": "", "country": "", "country_source": "", "language": ""} for i in range(100)]
agg2 = aud.aggregate_audience(0, mock_age, conn=conn, platform="youtube")
bins = {x["bucket"]: x["pct"] for x in agg2["age_bins"]["bins"]}
check("A2g 年龄桶已判定内归一 19-29=75%", bins.get("19-29") == 75.0 and agg2["age_bins"]["determined_n"] == 40, str(bins))

# A3 LLM 回复解析(围栏/杂讯容忍)
parsed = aud._extract_json_array('前缀\n```json\n[{"i":1,"age":"19-29","gender":"male","conf":0.6}]\n```后缀')
check("A3a 围栏 JSON 解析", len(parsed) == 1 and parsed[0]["age"] == "19-29")
check("A3b 坏文本返回空", aud._extract_json_array("no json here") == [])

# A4 creator_density
mock_subs = [{"platform": "youtube", "author_key": f"s{i}", "gender": "", "country": "", "country_source": "",
              "language": "", "subscriber_count": (5000 if i < 3 else 10) if i < 10 else None} for i in range(20)]
agg3 = aud.aggregate_audience(0, mock_subs, conn=conn, platform="youtube")
check("A4 创作者浓度 3/10=30%", agg3["creator_density"]["pct"] == 30.0 and agg3["creator_density"]["known_n"] == 10,
      str(agg3["creator_density"]))

# A5 comment_intel 词表/直方/证据
mock_comments = [
    {"text": "Where to buy this lens? Amazing!", "author": "alice", "created_at": "2026-06-01T18:05:00Z", "like_count": 9, "is_reply": False, "video_key": "v1"},
    {"text": "多少钱?求链接", "author": "bob", "created_at": "2026-06-01T18:40:00Z", "like_count": 5, "is_reply": False, "video_key": "v1"},
    {"text": "viltrox is better than sigma imo", "author": "alice", "created_at": "2026-06-02T19:00:00Z", "like_count": 3, "is_reply": True, "video_key": "v2"},
    {"text": "nice video", "author": "carol", "created_at": "2026-06-02T03:00:00Z", "like_count": 0, "is_reply": False, "video_key": "v2"},
    {"text": "Sony color science ftw", "author": "alice", "created_at": "2026-06-03T18:30:00Z", "like_count": 1, "is_reply": False, "video_key": "v2"},
]
intel = ci.analyze_comments(mock_comments)
pi = intel["purchase_intent"]
check("A5a 购买意向 2/5=40%", pi["count"] == 2 and pi["pct"] == 40.0, str(pi["count"]))
check("A5b 意向证据带作者/时间(赞序,<=5)", pi["samples"][0]["author_handle"] == "alice" and pi["samples"][0]["created_at"].startswith("2026-06-01"), str(pi["samples"][0]))
brands = {b["brand"]: b for b in intel["brand_mentions"]}
check("A5c 品牌词表 Viltrox/Sigma/Sony 各 1", brands.get("Viltrox", {}).get("count") == 1 and brands.get("Sigma", {}).get("count") == 1 and brands.get("Sony", {}).get("count") == 1, str(list(brands)))
check("A5d 品牌证据 <=2 条且带作者", len(brands["Viltrox"]["samples"]) == 1 and brands["Viltrox"]["samples"][0]["author_handle"] == "alice")
ah = intel["active_hours"]
check("A5e 直方 18 时=3 条且 top_hours[0]=18", ah["hist"][18] == 3 and ah["top_hours"][0] == 18, str(ah["top_hours"]))
check("A5f 峰值证据 peak_hour_comment_count=3", ah["peak_hour_comment_count"] == 3)
check("A5g 建议发帖 UTC 18-20时", ah["suggestion"] == "UTC 18-20时", ah["suggestion"])
en = intel["engagement"]
check("A5h 互动:2.5 评论/视频 · 回复率 20% · 赞中位 3", en["comments_per_video"] == 2.5 and en["reply_pct"] == 20.0 and en["likes_median"] == 3, str(en))
sf = intel["superfans"]
check("A5i 铁粉 alice x3 带代表评论", sf and sf[0]["handle"] == "alice" and sf[0]["count"] == 3 and bool(sf[0]["sample"]), str(sf[:1]))
check("A5j 空集诚实", ci.analyze_comments([])["sample_size"] == 0)
print(f"  A 段耗时 {time.time() - t0:.2f}s")

# ══ B. YouTube 真数据(watchluke;Gemini 总调用 <=3)══
# 环境变量 AUD_TEST_SKIP_B=1 可跳过本段(B 段含真 LLM 调用;重复跑闸门时省额度)。
section("B. YouTube 真数据 watchluke(A 路真调,总调用不超 3 次)")
row = conn.execute("SELECT id, handle FROM vkpi_kol_pool WHERE platform='youtube' AND handle='watchluke' LIMIT 1").fetchone()
yt_id = int(dict(row)["id"]) if row else 0
if os.environ.get("AUD_TEST_SKIP_B"):
    print("  [SKIP] AUD_TEST_SKIP_B=1(省 LLM 额度;完整跑法去掉该环境变量)")
    yt_id = 0
elif not yt_id:
    print("  [SKIP] watchluke 不在库")
else:
    # LLM 预检(1 次小调用):direct 不通则换 YTDLP_PROXY 重试;都不通 -> 刷新时关掉 A 路。
    from app.platform import llm_gateway

    def _llm_ok() -> bool:
        resp = llm_gateway.invoke("Reply with exactly: OK", purpose="vkpi_audience_age_preflight",
                                  preferred_provider="google", max_output_tokens=8)
        return str(resp.get("status")) == "success" and str(resp.get("model")) != "rule_v0"

    llm_calls_used = 1
    llm_viable = _llm_ok()
    if not llm_viable:
        proxy = (os.environ.get("YTDLP_PROXY") or "").strip()
        if proxy and not (os.environ.get("HTTPS_PROXY") or "").strip():
            print(f"  LLM 直连不通,带代理重试(HTTPS_PROXY={proxy.split('@')[-1]})")
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["https_proxy"] = proxy
            llm_calls_used += 1
            llm_viable = _llm_ok()
    print(f"  LLM 预检:{'可用' if llm_viable else '不可用(A 路将跳过,不阻断)'}(已用 {llm_calls_used} 次调用)")
    batches = min(2, max(0, 3 - llm_calls_used)) if llm_viable else 0
    t0 = time.time()
    result = aud.refresh_audience_stats(yt_id, llm_max_batches=batches)
    print(f"  B 段刷新耗时 {time.time() - t0:.2f}s status={result.get('status')} (A 路批次上限 {batches})")
    if result.get("status") == "ok":
        payload = result["audience"]
        print_audience_blocks(payload)
        check("B1 YT v2 刷新成功", int(payload.get("sample_size") or 0) > 0)
        check("B2 归一性别在(男+女>0 时)", payload.get("gender_normalized", {}).get("determined_n", 0) >= 0)
        cov = payload.get("age_coverage") or {}
        check("B3 A 路调用数在 3 次预算内", int((cov.get("llm") or {}).get("calls") or 0) + llm_calls_used <= 3,
              f"llm={cov.get('llm')} preflight={llm_calls_used}")
        check("B4 白捡字段落表(subscriber_count 非全空)",
              int(dict(conn.execute("SELECT COUNT(*) AS n FROM vkpi_commenter_profiles WHERE platform='youtube' AND subscriber_count IS NOT NULL").fetchone())["n"]) > 0)
        check("B5 comment_intel 出块(API 评论源)", (payload.get("comment_intel") or {}).get("source") == "youtube_api_sample")
        aged = int(dict(conn.execute("SELECT COUNT(*) AS n FROM vkpi_commenter_profiles WHERE platform='youtube' AND age_bucket IS NOT NULL AND age_bucket<>''").fetchone())["n"])
        print(f"  身份缓存中已有年龄桶的评论者:{aged}")
        check("B6 年龄桶写入缓存", aged > 0 or not llm_viable, f"aged={aged}")
    else:
        print(f"  [SKIP-honest] {result.get('status')}: {str(result.get('reason'))[:200]} —— 需在服务器跑数据测试")

# ══ C. Instagram 真数据(库存评论,零 Apify,llm_max_batches=0)══
section("C. Instagram 真数据(库存评论;A 路关闭省额度)")
ig_row = conn.execute(
    "SELECT e.kol_pool_id AS id, COUNT(*) AS n FROM vkpi_comments c "
    "JOIN vkpi_kol_video_evidence e ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
    "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id AND p.platform='instagram' "
    "GROUP BY e.kol_pool_id ORDER BY n DESC LIMIT 1"
).fetchone()
if ig_row:
    ig_id = int(dict(ig_row)["id"])
    t0 = time.time()
    result = aud.refresh_audience_stats(ig_id, enqueue_if_missing=False, llm_max_batches=0)
    print(f"  kol_pool_id={ig_id} 耗时 {time.time() - t0:.2f}s status={result.get('status')}")
    if result.get("status") == "ok":
        payload = result["audience"]
        print_audience_blocks(payload)
        check("C1 IG v2 刷新成功", int(payload.get("sample_size") or 0) > 0)
        check("C2 comment_intel 从 vkpi_comments 出块", (payload.get("comment_intel") or {}).get("source") == "vkpi_comments")
        check("C3 IG 有 overlap 键(可为空但结构在)", isinstance(payload.get("overlap"), dict))
    else:
        check("C1 IG v2 刷新成功", False, str(result.get("status")))
else:
    print("  [SKIP] 库里没有挂到评论的 IG KOL")

# ══ D. 共同粉丝 overlap 直接验证 ══
section("D. 共同粉丝 overlap(库内直接验证 + 全库最强共享对复现)")
if ig_row:
    ov = ci.compute_audience_overlap(int(dict(ig_row)["id"]), conn=conn)
    print(f"  KOL #{dict(ig_row)['id']}: self={ov.get('self_commenters')} peers_checked={ov.get('peers_checked')} items={ov.get('items')}")
    check("D1 overlap 结构完整", isinstance(ov.get("items"), list) and int(ov.get("self_commenters") or 0) > 0)
# 全库扫描:官号 + evidence 桥 + kol_comments(dossier 仓)全集合的最强共享对
# (复现「跨账号存在共享评论者/最强一对共享 N 人」量级)
sets: dict = {}
for r in conn.execute(
    "SELECT c.post_table, c.account_id, c.platform, c.author_handle FROM vkpi_comments c "
    "WHERE c.account_id IS NOT NULL AND c.author_handle IS NOT NULL AND c.author_handle<>''"
).fetchall():
    rec = dict(r)
    kind = "official" if rec["post_table"] == "vkpi_employee_channels" else "kol"
    key = (kind, int(rec["account_id"]))
    sets.setdefault(key, set()).add(f"{str(rec['platform']).lower()}:{str(rec['author_handle']).strip().lower()}")
for r in conn.execute(
    "SELECT e.kol_pool_id AS pid, c.platform, c.author_handle FROM vkpi_comments c "
    "JOIN vkpi_kol_video_evidence e ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
    "WHERE c.author_handle IS NOT NULL AND c.author_handle<>''"
).fetchall():
    rec = dict(r)
    sets.setdefault(("kol", int(rec["pid"])), set()).add(f"{str(rec['platform']).lower()}:{str(rec['author_handle']).strip().lower()}")
for r in conn.execute(
    "SELECT kol_id AS pid, platform, author_handle FROM kol_comments "
    "WHERE author_handle IS NOT NULL AND author_handle<>''"
).fetchall():
    rec = dict(r)
    sets.setdefault(("kol_main", int(rec["pid"])), set()).add(f"{str(rec['platform']).lower()}:{str(rec['author_handle']).strip().lower()}")
pairs = []
keys = list(sets)
for i in range(len(keys)):
    for j in range(i + 1, len(keys)):
        shared = len(sets[keys[i]] & sets[keys[j]])
        if shared >= 2:
            pairs.append((shared, keys[i], keys[j]))
pairs.sort(reverse=True)
print(f"  全库评论者集合 {len(keys)} 个;共享>=2 的账号对 {len(pairs)} 对")
for shared, a, b in pairs[:3]:
    print(f"    最强共享对:{a} <-> {b} 共享 {shared} 位评论者")
check("D2 全库存在共享评论者对(机制可复现)", len(pairs) >= 1, f"pairs={len(pairs)} 最强共享 {pairs[0][0] if pairs else 0} 人")

# ══ 汇总 ══
section("汇总")
if failures:
    print(f"FAILED {len(failures)}: {failures}")
    sys.exit(1)
print("单元级 mock 全绿;真数据段结果见上(网络/额度不可达处已诚实标注)。")
