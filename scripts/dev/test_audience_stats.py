"""受众画像 ensemble_v1 本地测试脚本(P0 验收用)。

跑法:.venv/bin/python scripts/dev/test_audience_stats.py

覆盖:
  A. 单元级(mock 数据,必须全绿):三层推断 / 聚合 / 经验贝叶斯收缩。
  B. YouTube 真数据:对库里真实 YT 频道跑 refresh_audience_stats;直连失败自动带
     HTTPS_PROXY=YTDLP_PROXY 重试;再失败诚实报告「需在服务器跑数据测试」。
  C. Instagram 真数据:用库里已有评论的 KOL(评论桥 vkpi_comments)跑真聚合。
  D. TikTok:库里无评论 -> 验证 pending_comments 分支(测试里不真入队,保 Apify 预算)。

红线:绝不写 viltrox_fit_score、不碰 rule_v0。只写 audience_estimated_json / 身份缓存表。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(1, str(ROOT / "scripts"))

from stdout_utils import out  # noqa: E402


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

PASS = "  [PASS]"
FAIL = "  [FAIL]"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    line = f"{PASS if condition else FAIL} {name}" + (f" — {detail}" if detail else "")
    out(line)
    if not condition:
        failures.append(name)


def section(title: str) -> None:
    out("\n" + "=" * 62)
    out(title)
    out("=" * 62)


# ── A. 单元级(mock 数据)──
section("A. 单元级 mock 测试(推断 / 聚合 / 收缩)")
t0 = time.time()

# A1 三层国家推断
r = aud.infer_commenter({"platform": "youtube", "author_key": "a1", "display_name": "Random Person", "comment_text": "great video", "declared_country": "US"})
check("A1a 自报国家优先", r["country"] == "US" and r["country_source"] == "declared" and r["country_conf"] == 0.9, str(r["country"]))
r = aud.infer_commenter({"platform": "youtube", "author_key": "a2", "display_name": "Giuseppe Verdi", "comment_text": "", "declared_country": ""})
check("A1b 人名词表国籍猜(Giuseppe->IT, .4)", r["country"] == "IT" and r["country_source"] == "name" and r["country_conf"] == 0.4, str(r))
r = aud.infer_commenter({"platform": "instagram", "author_key": "a3", "display_name": "xx99", "comment_text": "مرحبا صديقي كيف حالك", "declared_country": ""})
check("A1c 评论语言推市场(ar->SA, .3)", r["country"] == "SA" and r["country_source"] == "language" and r["country_conf"] == 0.3, str(r["country"]))
r = aud.infer_commenter({"platform": "tiktok", "author_key": "a4", "display_name": "zq8_x", "comment_text": "!!", "declared_country": ""})
check("A1d 无信号诚实留空", r["country"] == "" and r["gender"] == "", str(r))

# A2 性别人名表
r = aud.infer_commenter({"platform": "youtube", "author_key": "b1", "display_name": "Michael Chen", "comment_text": "", "declared_country": ""})
check("A2a 男名(.8)", r["gender"] == "male" and r["gender_conf"] == 0.8)
r = aud.infer_commenter({"platform": "youtube", "author_key": "b2", "display_name": "jessica_travels", "comment_text": "", "declared_country": ""})
check("A2b 女名 handle 风格(.8)", r["gender"] == "female" and r["gender_conf"] == 0.8)

# A3 聚合(合成 100 人:60 US 自报 / 20 名字 DE / 20 无国家;男40 女30 未知30)
mock = []
for i in range(100):
    c = {"platform": "youtube", "author_key": f"m{i}", "display_name": "", "comment_text": "this is great thank you", "declared_country": ""}
    if i < 60:
        c["declared_country"] = "US"
    elif i < 80:
        c["display_name"] = "Hans Gruber"
    if i < 40:
        c["display_name"] = c["display_name"] or "James Smith"
    elif i < 70:
        c["display_name"] = "Emily Stone" if i >= 60 else c["display_name"] or "Emily Stone"
    mock.append(aud.infer_commenter(c))
agg = aud.aggregate_audience(0, mock, conn=get_conn(), platform="youtube")
gsum = sum(agg["gender"].values())
check("A3a 样本量=100", agg["sample_size"] == 100)
check("A3b 性别环三段和≈100", 99.0 <= gsum <= 101.0, f"sum={gsum}")
check("A3c US 进 top_countries 首位", (agg["top_countries"] or [{}])[0].get("code") == "US", str(agg["top_countries"][:3]))
check("A3d coverage 自报=60%", agg["coverage"]["declared_pct"] == 60.0, str(agg["coverage"]))
check("A3e 置信度在 (0, 0.9]", 0 < agg["confidence"] <= 0.9, str(agg["confidence"]))
check("A3f method/beta 标注", agg.get("method") == "ensemble_v1" and agg.get("beta") is True)

# A4 收缩(n=50, tau=50 -> weight 0.5)
import copy

def payload_fresh():
    return copy.deepcopy({
        "gender": {"male_pct": 80.0, "female_pct": 10.0, "unknown_pct": 10.0},
        "top_countries": [{"code": "US", "pct": 40.0}, {"code": "DE", "pct": 10.0}],
    })

payload = payload_fresh()
prior = {"n": 3, "gender": {"male_pct": 40.0, "female_pct": 40.0, "unknown_pct": 20.0}, "countries": {"US": 20.0, "JP": 30.0}}
shrunk = aud._apply_shrinkage(dict(payload), prior, n=50, tau=50.0)
check("A4a 男 pct 收缩 80->60", shrunk["gender"]["male_pct"] == 60.0, str(shrunk["gender"]))
us = next((c for c in shrunk["top_countries"] if c["code"] == "US"), {})
jp = next((c for c in shrunk["top_countries"] if c["code"] == "JP"), {})
check("A4b US 收缩 (0.5*40+0.5*20)=30", us.get("pct") == 30.0, str(shrunk["top_countries"]))
check("A4c prior 独有国 JP 以半权进入", jp.get("pct") == 15.0, str(jp))
check("A4d shrinkage 元数据", shrunk["shrinkage"]["applied"] is True and shrunk["shrinkage"]["weight"] == 0.5)
noprior = aud._apply_shrinkage(payload_fresh(), None, n=50)
check("A4e 无先验跳过(原样返回)", noprior["shrinkage"]["applied"] is False and noprior["gender"]["male_pct"] == 80.0)
out(f"  A 段耗时 {time.time() - t0:.2f}s")

# ── B. YouTube 真数据 ──
section("B. YouTube 真数据(Data API;被墙自动换代理重试)")
conn = get_conn()
# 注:compat 层 SQL 文本里不能出现字符 percent(psycopg 会当占位符),故不用 LIKE,改 POSITION 排除 UC 开头 id。
row = conn.execute(
    "SELECT id, handle, followers FROM vkpi_kol_pool WHERE platform='youtube' AND handle IS NOT NULL AND handle<>'' "
    "AND POSITION('UC' in handle) <> 1 ORDER BY followers DESC NULLS LAST LIMIT 1"
).fetchone()
yt_id = int(dict(row)["id"]) if row else 0
yt_handle = str(dict(row)["handle"]) if row else "@CurrenSheldon"
out(f"  目标 KOL:kol_pool_id={yt_id} handle={yt_handle}")
t0 = time.time()
result = aud.refresh_audience_stats(yt_id) if yt_id else {"status": "skipped", "reason": "no youtube kol in pool"}
if result.get("status") == "network_error":
    proxy = (os.environ.get("YTDLP_PROXY") or "").strip()
    if proxy and not (os.environ.get("HTTPS_PROXY") or "").strip():
        out(f"  直连失败({str(result.get('reason'))[:120]}),带 HTTPS_PROXY={proxy.split('@')[-1]} 重试…")
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["https_proxy"] = proxy
        result = aud.refresh_audience_stats(yt_id)
out(f"  B 段耗时 {time.time() - t0:.2f}s status={result.get('status')}")
if result.get("status") == "ok":
    audp = result.get("audience") or {}
    out(f"  样本 {audp.get('sample_size')} 评论者 / 扫描 {audp.get('comments_scanned')} 评论 / 置信 {audp.get('confidence')}")
    out(f"  性别 {audp.get('gender')}")
    out(f"  Top countries {audp.get('top_countries')}")
    out(f"  语言 {(audp.get('languages') or [])[:5]}")
    out(f"  覆盖 {audp.get('coverage')} / 收缩 {audp.get('shrinkage')}")
    check("B1 YT 真数据聚合成功", int(audp.get("sample_size") or 0) > 0)
    cached = conn.execute("SELECT COUNT(*) AS n FROM vkpi_commenter_profiles WHERE platform=?", ("youtube",)).fetchone()
    check("B2 身份缓存已落表", int(dict(cached)["n"]) > 0, f"{dict(cached)['n']} rows")
elif result.get("status") == "network_error":
    out("  [SKIP-honest] YouTube API 直连与代理均不通 —— 需在服务器跑数据测试。")
    out(f"  reason: {str(result.get('reason'))[:200]}")
else:
    out(f"  [SKIP-honest] {result.get('status')}: {str(result.get('reason'))[:200]}")

# ── C. Instagram 真数据(库里已有评论的 KOL)──
section("C. Instagram 真数据(vkpi_comments 评论桥)")
ig_row = conn.execute(
    "SELECT e.kol_pool_id AS id, COUNT(*) AS n FROM vkpi_comments c "
    "JOIN vkpi_kol_video_evidence e ON c.post_table IN ('evidence','vkpi_kol_video_evidence') AND c.post_id=e.id "
    "JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id AND p.platform='instagram' "
    "GROUP BY e.kol_pool_id ORDER BY n DESC LIMIT 1"
).fetchone()
if ig_row:
    ig_id = int(dict(ig_row)["id"])
    out(f"  目标 KOL:kol_pool_id={ig_id}(库存评论 {dict(ig_row)['n']} 条)")
    t0 = time.time()
    result = aud.refresh_audience_stats(ig_id, enqueue_if_missing=False)
    out(f"  C 段耗时 {time.time() - t0:.2f}s status={result.get('status')}")
    if result.get("status") == "ok":
        audp = result.get("audience") or {}
        out(f"  样本 {audp.get('sample_size')} 评论者 / 置信 {audp.get('confidence')}")
        out(f"  性别 {audp.get('gender')}")
        out(f"  Top countries {audp.get('top_countries')}")
        out(f"  语言 {(audp.get('languages') or [])[:5]}")
        out(f"  覆盖 {audp.get('coverage')}(IG 无自报路,declared 恒 0 —— 口径一致,置信自然降档)")
        check("C1 IG 真聚合成功", int(audp.get("sample_size") or 0) > 0)
        check("C2 IG declared_pct=0(无自报信号)", float(audp.get("coverage", {}).get("declared_pct") or 0) == 0.0)
    else:
        check("C1 IG 真聚合成功", False, f"{result.get('status')}: {str(result.get('reason'))[:160]}")
else:
    out("  [SKIP-honest] 库里没有任何 IG KOL 挂到已抓评论 —— IG 真聚合需先抓评论。")

# ── D. TikTok pending_comments 分支 ──
section("D. TikTok(无评论 -> pending_comments;测试不真入队,保 Apify 预算)")
tt_row = conn.execute(
    "SELECT id, handle FROM vkpi_kol_pool WHERE platform='tiktok' ORDER BY followers DESC NULLS LAST LIMIT 1"
).fetchone()
if tt_row:
    tt_id = int(dict(tt_row)["id"])
    out(f"  目标 KOL:kol_pool_id={tt_id} handle={dict(tt_row)['handle']}")
    result = aud.refresh_audience_stats(tt_id, enqueue_if_missing=False)
    out(f"  status={result.get('status')} comments_found={result.get('comments_found')}")
    check("D1 评论不足返回 pending_comments", result.get("status") == "pending_comments", str(result.get("status")))
    check("D2 enqueue_if_missing=False 时不入队", result.get("enqueued") is False)
    out("  (生产端点默认 enqueue_if_missing=True:会真入队抓评论并返回 enqueued=true)")
else:
    out("  [SKIP] 库里无 tiktok KOL。")

# ── 汇总 ──
section("汇总")
if failures:
    out(f"FAILED {len(failures)}: {failures}")
    sys.exit(1)
out("单元级 mock 全绿;真数据段结果见上(网络不可达时已诚实标注)。")
