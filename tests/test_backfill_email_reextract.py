"""L0 邮箱重抽回填脚本(scripts/backfill_email_reextract.py)的纯函数锁。

覆盖任务四类验收:
  1. 「换行吃字母」fixture:修复后的重抽链捞回干净邮箱,不再产 n 前缀假地址;
  2. 高置信不被覆盖:已在表的值(无论置信高低)绝不进插入计划;
  3. 嫌疑三型裁决路径:A 型(嫌疑=n+干净)/ B 型(嫌疑=干净缺首字母)/ 未决 report-only;
  4. 真 n 开头邮箱不被自我裁决误伤。
全部走 evaluate_kol/plan_new_emails/adjudicate_suspect 纯函数,不碰库、零网络。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_email_reextract.py"
_SPEC = importlib.util.spec_from_file_location("backfill_email_reextract", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = mod
_SPEC.loader.exec_module(mod)


def _full_scan_raw(text: str) -> dict:
    """埋进结构化字段够不到的嵌套位置,命中只能来自 raw_full_scan 兜底扫描。"""
    return {"latestPosts": [{"text": text}]}


def _suspect(sid: int, kol: int, value: str) -> dict:
    return {"id": sid, "kol_pool_id": kol, "contact_value": value,
            "verification_status": "observed"}


# ---- 1. 换行吃字母 fixture:重抽捞回干净值 ----

def test_newline_eaten_email_reextracted_clean() -> None:
    cands = mod.extract_email_candidates(
        _full_scan_raw("business inquiries below\nfoo@bar.com"), "instagram"
    )
    values = [c["contact_value"] for c in cands]
    assert "foo@bar.com" in values
    assert "nfoo@bar.com" not in values


def test_newline_eaten_email_flows_into_insert_plan() -> None:
    result = mod.evaluate_kol(
        _full_scan_raw("contact\nhello@withgar.com"), "youtube",
        existing_values=set(), suspect_rows=[],
    )
    assert [c["contact_value"] for c in result["to_insert"]] == ["hello@withgar.com"]
    assert result["skipped"] == []


# ---- 2. 不覆盖已有/更高置信来源 ----

def test_existing_value_never_replanned_regardless_of_confidence() -> None:
    # 表里已有 biz@creator.com(设想为 0.92 高置信 ig_business_profile 行);
    # 重抽同值(兜底扫描只有 0.45)必须整体跳过,不得进入写计划。
    result = mod.evaluate_kol(
        _full_scan_raw("reach me\nbiz@creator.com"), "instagram",
        existing_values={"biz@creator.com"}, suspect_rows=[],
    )
    assert result["to_insert"] == []
    assert [c["contact_value"] for c in result["skipped"]] == ["biz@creator.com"]


def test_existing_match_is_case_insensitive_and_new_value_still_inserts() -> None:
    to_insert, skipped = mod.plan_new_emails(
        [
            {"contact_type": "email", "contact_value": "Damien@dworld.pro"},
            {"contact_type": "email", "contact_value": "fresh@new.example.com"},
        ],
        existing_values={"damien@dworld.pro"},
    )
    assert [c["contact_value"] for c in to_insert] == ["fresh@new.example.com"]
    assert [c["contact_value"] for c in skipped] == ["Damien@dworld.pro"]


# ---- 3. 嫌疑三型裁决 ----

def test_adjudicate_type_a_n_prefix() -> None:
    verdict = mod.adjudicate_suspect("ncontact@moodydarkroom.com",
                                     ["contact@moodydarkroom.com"])
    assert verdict is not None
    kind, clean = verdict
    assert kind.startswith("A")
    assert clean == "contact@moodydarkroom.com"


def test_adjudicate_type_b_missing_first_letter() -> None:
    # 嫌疑 = 干净缺首字母:重抽出的干净值补全首字母(此处补 'i')。
    verdict = mod.adjudicate_suspect("n@dolomiti.it", ["in@dolomiti.it"])
    assert verdict is not None
    kind, clean = verdict
    assert kind.startswith("B")
    assert clean == "in@dolomiti.it"


def test_adjudicate_unresolved_stays_report_only() -> None:
    assert mod.adjudicate_suspect("n@viltrox.ph", ["hello@other.example.com"]) is None
    assert mod.adjudicate_suspect("n@viltrox.ph", []) is None


def test_adjudicate_never_self_matches_legit_n_email() -> None:
    # 真 n 开头邮箱:clean == 嫌疑本身不构成裁决(不误伤)。
    assert mod.adjudicate_suspect("nina@bar.com", ["nina@bar.com"]) is None
    # 但 nnina(转义腐蚀)+ 干净 nina 是标准 A 型。
    verdict = mod.adjudicate_suspect("nnina@bar.com", ["nina@bar.com"])
    assert verdict is not None and verdict[0].startswith("A")


def test_evaluate_kol_adjudicates_and_inserts_clean_value() -> None:
    # 端到端纯函数:raw 里换行+干净值;嫌疑行是 n 前缀假地址 ->
    # A 型裁决 + 干净值进插入计划(clean_in_table=False)。
    result = mod.evaluate_kol(
        _full_scan_raw("business inquiries\nfoo@bar.com"), "instagram",
        existing_values=set(),
        suspect_rows=[_suspect(29, 1512, "nfoo@bar.com")],
    )
    assert [c["contact_value"] for c in result["to_insert"]] == ["foo@bar.com"]
    assert len(result["decisions"]) == 1
    d = result["decisions"][0]
    assert d["kind"].startswith("A")
    assert d["clean_value"] == "foo@bar.com"
    assert d["clean_in_table"] is False
    assert result["unresolved"] == []


def test_evaluate_kol_clean_already_in_table_marks_flag() -> None:
    result = mod.evaluate_kol(
        _full_scan_raw("business inquiries\nfoo@bar.com"), "instagram",
        existing_values={"foo@bar.com"},
        suspect_rows=[_suspect(30, 1513, "nfoo@bar.com")],
    )
    assert result["to_insert"] == []  # 干净值已在表,不重插
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["clean_in_table"] is True


def test_evaluate_kol_unresolvable_suspect_untouched() -> None:
    result = mod.evaluate_kol(
        _full_scan_raw("no contact info here at all"), "instagram",
        existing_values=set(),
        suspect_rows=[_suspect(45, 1555, "n@dolomiti.it")],
    )
    assert result["decisions"] == []
    assert [s["id"] for s in result["unresolved"]] == [45]
