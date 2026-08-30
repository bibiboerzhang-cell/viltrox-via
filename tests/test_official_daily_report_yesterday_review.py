"""官号日报「昨日建议回看」prompt 注入 — characterization + 行为验收。

两条硬验收(任务书原文):
1. 有昨日报告时 prompt 含回看段(建议原文 + unverifiable 口径 + 收敛指令);
2. 无昨日报告时 prompt 与旧版逐字节一致(零回归证明:冻结旧实现原文 + 改前捕获的 SHA256 双锚)。
红线复核:读历史是纯 SELECT;输出 schema(JSON 字段)不变;不触预算闸。
"""
from __future__ import annotations

import hashlib

from app.domains.channels import official_daily_report as odr


# ---- 冻结的旧版 _build_prompt(改刀前源码逐字拷贝,作 characterization 基准) ----
def _frozen_old_build_prompt(report_text: str, handle: str, platform: str, language: str) -> str:
    lang_line = "用中文输出。" if language != "en" else "Output in English."
    return (
        f"你是 Viltrox(唯卓仕)的官方社媒运营负责人。下面是官号 {platform}:{handle} 的真实绩效数据。\n"
        "请生成一份【今日账号分析评估报告】,给运营看,要可执行。要求:\n"
        "- 只基于下面提供的数据做分析,绝不编造数字;标注「待接入/pending/无数据」的缺口要点明是待补,不要硬编结论。\n"
        "- 结论引用具体数字 / 帖子标题 / 评论原文。\n"
        "- 画面质量:若数据【画面质量】段含 Gemini 真实分析(content_quality 均分等),据此点评本号视频画质\n"
        "  水平与高/低分内容差异;若标 pending,则诚实说明真画质分仍在增量分析中、据标题间接评估,不要编造。\n"
        "- 评论洞察:据评论原文归纳受众情绪(正/负/咨询)、高频诉求、负面点(如不支持某卡口、价格、缺货)。\n"
        "- 提升建议:基于本号近况给 3-5 条可执行建议(发什么内容 / 哪类帖该多发 / 怎么回应评论诉求 / 互动率怎么提)。\n"
        "- 每个文本字段控制在 3-4 句精炼输出,别长篇大论(护成本 + 防截断)。\n"
        f"- {lang_line}\n\n"
        "账号数据:\n"
        '"""\n'
        f"{report_text}\n"
        '"""\n\n'
        "严格只输出 JSON(无多余文字 / 无 markdown 代码块):\n"
        "{\n"
        '  "play_performance": "播放表现评估:整体播放走向 + 哪条帖爆/弱 + 为什么(引用数字)",\n'
        '  "comment_insights": "评论洞察:情绪倾向 + 高频诉求 + 负面点(引用评论原文)",\n'
        '  "visual_quality": "画面质量(诚实说明真视觉分析待接入,据现有信息间接评估内容倾向)",\n'
        '  "data_trend": "数据趋势:粉丝/互动/增长走向(增长/停滞/负增长,引用 delta)",\n'
        '  "suggestions": ["可执行提升建议(谁/做什么)", "...3-5 条"],\n'
        '  "headline": "一句话今日总评"\n'
        "}\n"
    )


_SAMPLES = [
    ("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "zh"),
    ("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "en"),
    ("", "", "", "zh"),
]
# 改刀前在真实模块上捕获的 SHA256(2026-08-30),锚死冻结拷贝本身没抄错
_PRECHANGE_SHA256 = [
    "57f34a3f1b026d1392114ca7872a446b2a6ee773a5004d5dc714f131d4d7cfea",
    "b751117ca5f1f3875ee17b6c8e29c1711be35be2701470d329f01246d1fc1b20",
    "6614382def1c0e291d2e6249e22cd92d30a840470001d116ad8f3dde06d85c33",
]


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """只需伺候 _yesterday_suggestions 的单条 SELECT。"""

    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _FakeCursor(self.row)


def _yday_row(suggestions) -> dict:
    import json

    return {"report_json": json.dumps({"analysis": {"suggestions": suggestions}, "facts_summary": {}}, ensure_ascii=False)}


# ---------------- 零回归:无昨日报告 → 逐字节一致 ----------------

def test_frozen_copy_matches_prechange_hashes():
    for sample, expected in zip(_SAMPLES, _PRECHANGE_SHA256):
        got = hashlib.sha256(_frozen_old_build_prompt(*sample).encode("utf-8")).hexdigest()
        assert got == expected, f"冻结旧版拷贝与改前捕获哈希不符: {sample}"


def test_prompt_without_review_is_byte_identical_to_old():
    for sample in _SAMPLES:
        assert odr._build_prompt(*sample) == _frozen_old_build_prompt(*sample)
        assert odr._build_prompt(*sample, yesterday_review="") == _frozen_old_build_prompt(*sample)


def test_no_yesterday_report_yields_empty_block_and_old_prompt():
    conn = FakeConn(row=None)
    block = odr._yesterday_review_block(conn, 7, "2026-08-30")
    assert block == ""
    prompt = odr._build_prompt("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "zh", yesterday_review=block)
    assert prompt == _frozen_old_build_prompt("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "zh")


def test_bad_payloads_all_degrade_to_empty_block():
    assert odr._yesterday_review_block(FakeConn(row={"report_json": "not-json{"}), 7, "2026-08-30") == ""
    assert odr._yesterday_review_block(FakeConn(row=_yday_row([])), 7, "2026-08-30") == ""
    assert odr._yesterday_review_block(FakeConn(row=_yday_row("不是列表")), 7, "2026-08-30") == ""
    assert odr._yesterday_review_block(FakeConn(row={"report_json": None}), 7, "2026-08-30") == ""


def test_bad_report_date_short_circuits_before_sql():
    conn = FakeConn(row=_yday_row(["a"]))
    assert odr._yesterday_review_block(conn, 7, "not-a-date") == ""
    assert conn.calls == []  # 日期解析失败 → 连 SELECT 都不发


# ---------------- 有昨日报告:回看段进 prompt ----------------

def test_with_yesterday_report_prompt_contains_review_section():
    conn = FakeConn(row=_yday_row(["多发 85mm 人像实拍", "置顶回复卡口咨询"]))
    block = odr._yesterday_review_block(conn, 7, "2026-08-30")
    assert block != ""
    # 纯 SELECT + 参数指向昨日行
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert params == (7, "2026-08-29")
    # 段内容:日期、建议原文、unverifiable 口径、收敛指令
    assert "【昨日(2026-08-29)建议回看】" in block
    assert "多发 85mm 人像实拍" in block and "置顶回复卡口咨询" in block
    assert "unverifiable" in block
    assert "收敛" in block
    assert block.endswith("\n\n")
    # 注入后:prompt 含回看段,且剥掉回看段即旧版逐字节(schema 段未被动)
    prompt = odr._build_prompt("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "zh", yesterday_review=block)
    assert block in prompt
    assert prompt.replace(block, "", 1) == _frozen_old_build_prompt("TEXT-BLOCK\nline2 数据", "viltroxglobal", "youtube", "zh")
    assert prompt.index(block) < prompt.index("账号数据:")


def test_suggestions_clamped_to_six_and_truncated():
    many = [f"建议{i}-" + "长" * 300 for i in range(10)]
    conn = FakeConn(row=_yday_row(many))
    yday, sugs = odr._yesterday_suggestions(conn, 7, "2026-08-30")
    assert yday == "2026-08-29"
    assert len(sugs) == 6
    block = odr._yesterday_review_block(FakeConn(row=_yday_row(many)), 7, "2026-08-30")
    assert "建议9" not in block  # 第 7 条起被裁
    for line in block.splitlines():
        if line.startswith("  1. "):
            assert len(line) <= 5 + 160  # 单条截断 160 字


# ---------------- 接线:generate_one 真把回看段送进 LLM prompt ----------------

def test_generate_one_wires_review_into_prompt(monkeypatch):
    captured: dict[str, str] = {}
    conn = FakeConn(row=_yday_row(["昨日建议A"]))
    monkeypatch.setattr(odr, "get_conn", lambda: conn)
    monkeypatch.setattr(odr, "_account_data", lambda c, ch: {"trend_7d": [{"snapshot_date": "2026-08-30"}], "posts": {}, "comments": {}, "channel_id": 7})
    monkeypatch.setattr(odr.budget_guard, "check_budget", lambda scope, cost: True)
    monkeypatch.setattr(odr, "_report_text", lambda data: "FACTS")

    def fake_generate(prompt):
        captured["prompt"] = prompt
        return "", ""  # 走 analysis_unavailable 早退,不触 _store

    monkeypatch.setattr(odr, "_generate", fake_generate)
    result = odr.generate_one({"id": 7, "account_handle": "h", "platform": "youtube"}, report_date="2026-08-30")
    assert result["status"] == "analysis_unavailable"
    assert "【昨日(2026-08-29)建议回看】" in captured["prompt"]
    assert "昨日建议A" in captured["prompt"]
