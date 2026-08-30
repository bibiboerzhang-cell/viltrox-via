"""分支覆盖冲刺·legacy_import/legacy_import_audit.py — 旧表审计解析器的字段归一/风险分级。

覆盖:表头归一两级匹配、平台别名+URL 回退、handle 抽取逐平台分支、金额/币种解析、
CSV/XLSX 真实文件解析(临时文件)、审计端到端 issues/risk/去重分支。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.legacy_import import legacy_import_audit as la  # noqa: E402


class HeaderFieldTests(unittest.TestCase):
    def test_exact_alias_match(self):
        self.assertEqual(la._header_field("Platform"), "platform")
        self.assertEqual(la._header_field("红人"), "handle")
        self.assertEqual(la._header_field("联系邮箱"), "email")

    def test_compaction_strips_punctuation(self):
        self.assertEqual(la._header_field("KOL_Name"), "handle")
        self.assertEqual(la._header_field("profile url"), "profile_url")

    def test_substring_fallback_needs_long_alias(self):
        self.assertEqual(la._header_field("primary contactemail address"), "email")
        self.assertEqual(la._header_field("完全不认识的列"), "")
        self.assertEqual(la._header_field(""), "")


class NormalizePlatformTests(unittest.TestCase):
    def test_direct_aliases(self):
        self.assertEqual(la._normalize_platform("IG"), "instagram")
        self.assertEqual(la._normalize_platform("tik_tok"), "tiktok")
        self.assertEqual(la._normalize_platform("b站"), "bilibili")
        self.assertEqual(la._normalize_platform("Twitter"), "x")

    def test_multi_value_takes_first_recognized(self):
        self.assertEqual(la._normalize_platform("youtube, ig"), "youtube")
        self.assertEqual(la._normalize_platform("unknown; xhs"), "xiaohongshu")

    def test_url_fallback_host_mapping(self):
        self.assertEqual(la._normalize_platform("", "see https://youtu.be/abc"), "youtube")
        self.assertEqual(la._normalize_platform("", "https://www.tiktok.com/@a"), "tiktok")
        self.assertEqual(la._normalize_platform("", "no links here"), "")

    def test_unknown_platform_empty(self):
        # 注意选词:子串回退很激进("pigeon" 会吃 "ig"),这里用真正无别名子串的词
        self.assertEqual(la._normalize_platform("morse code"), "")


class UrlAndHandleTests(unittest.TestCase):
    def test_first_url_variants(self):
        self.assertEqual(la._first_url("go https://a.com/x, then more"), "https://a.com/x")
        # 裸域名补 https,尾部标点(.).] 被剥掉
        self.assertEqual(la._first_url("see www.site.com/page."), "https://www.site.com/page")
        self.assertEqual(la._first_url("no url"), "")

    def test_handle_from_instagram_and_tiktok(self):
        self.assertEqual(la._extract_handle_from_url("https://instagram.com/@alice/", "instagram"), "alice")
        self.assertEqual(la._extract_handle_from_url("https://tiktok.com/@bob/video/1", "tiktok"), "bob")
        self.assertEqual(la._extract_handle_from_url("https://tiktok.com/discover/x", "tiktok"), "discover")

    def test_handle_from_youtube_forms(self):
        self.assertEqual(la._extract_handle_from_url("https://youtube.com/@creator/videos", "youtube"), "creator")
        self.assertEqual(la._extract_handle_from_url("https://youtube.com/channel/UC123", "youtube"), "UC123")
        self.assertEqual(la._extract_handle_from_url("https://youtube.com/c/MyChan", "youtube"), "MyChan")
        self.assertEqual(la._extract_handle_from_url("https://youtube.com/user/old", "youtube"), "old")

    def test_handle_from_cn_platforms_and_default(self):
        self.assertEqual(
            la._extract_handle_from_url("https://xiaohongshu.com/user/profile/abc123", "xiaohongshu"),
            "abc123",
        )
        self.assertEqual(la._extract_handle_from_url("https://space.bilibili.com/space/998", "bilibili"), "998")
        self.assertEqual(la._extract_handle_from_url("https://site.com/@someone/extra", "website"), "someone")
        self.assertEqual(la._extract_handle_from_url("https://site.com", "website"), "")
        self.assertEqual(la._extract_handle_from_url("", "instagram"), "")

    def test_normalize_handle_paths(self):
        self.assertEqual(la._normalize_handle(""), "")
        self.assertEqual(la._normalize_handle("https://instagram.com/alice", "instagram"), "alice")
        self.assertEqual(la._normalize_handle("@bob, backup"), "bob")
        self.assertEqual(la._normalize_handle(" spaced name "), "spacedname")


class ContactAndAmountTests(unittest.TestCase):
    def test_find_email_scans_multiple_values(self):
        self.assertEqual(la._find_email("nothing", "hit me a@b.co thanks"), "a@b.co")
        self.assertEqual(la._find_email("no email"), "")

    def test_find_phone(self):
        self.assertEqual(la._find_phone("call +86 138-0000-1111 now"), "+86 138-0000-1111")
        self.assertEqual(la._find_phone("no digits"), "")

    def test_currency_symbols(self):
        self.assertEqual(la._currency_from("¥1200"), "CNY")
        self.assertEqual(la._currency_from("about 300 EUR"), "EUR")
        self.assertEqual(la._currency_from("plain 300"), "")

    def test_parse_amount_branches(self):
        self.assertEqual(la._parse_amount(""), (None, ""))
        self.assertEqual(la._parse_amount("$1,200.50"), (1200.5, "USD"))
        self.assertEqual(la._parse_amount("free of charge"), (None, ""))
        self.assertEqual(la._parse_amount("-30 usd"), (-30.0, "USD"))

    def test_cell_ref_to_index(self):
        self.assertEqual(la._cell_ref_to_index("A1"), 0)
        self.assertEqual(la._cell_ref_to_index("B2"), 1)
        self.assertEqual(la._cell_ref_to_index("AA10"), 26)
        self.assertEqual(la._cell_ref_to_index(""), 0)


class MapFieldsTests(unittest.TestCase):
    def test_first_value_wins_and_fallbacks_fill(self):
        raw = {
            "Platform": "youtube",
            "渠道": "tiktok",  # platform 已占位,不覆盖
            "notes": "reach me at x@y.com via https://youtube.com/@a",
        }
        mapped = la._map_fields(raw)
        self.assertEqual(mapped["platform"], "youtube")
        self.assertEqual(mapped["email"], "x@y.com")
        self.assertEqual(mapped["profile_url"], "https://youtube.com/@a")


class RiskLevelTests(unittest.TestCase):
    def test_three_levels(self):
        self.assertEqual(la._risk_level(["missing_platform"]), "high")
        self.assertEqual(la._risk_level(["missing_contact"]), "medium")
        self.assertEqual(la._risk_level([]), "low")


class ReadLegacyRowsTests(unittest.TestCase):
    def test_missing_file_and_unsupported_suffixes(self):
        with self.assertRaises(FileNotFoundError):
            la.read_legacy_rows("/nonexistent/file.csv")
        with tempfile.TemporaryDirectory() as tmp:
            xls = Path(tmp) / "old.xls"
            xls.write_bytes(b"x")
            with self.assertRaises(ValueError):
                la.read_legacy_rows(xls)
            other = Path(tmp) / "data.txt"
            other.write_text("x")
            with self.assertRaises(ValueError):
                la.read_legacy_rows(other)

    def test_csv_rows_skip_blank_and_honor_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "list.csv"
            csv_path.write_text(
                "Platform,Handle\nyoutube,alice\n,\ntiktok,bob\nig,carol\n",
                encoding="utf-8",
            )
            rows = la.read_legacy_rows(csv_path)
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["raw"], {"Platform": "youtube", "Handle": "alice"})
            self.assertEqual(rows[0]["row_number"], 2)
            capped = la.read_legacy_rows(csv_path, max_rows=1)
            self.assertEqual(len(capped), 1)


def _write_minimal_xlsx(path: Path) -> None:
    """手工拼最小 xlsx:共享串/inlineStr/布尔/列空洞/双 sheet 全都有。"""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    workbook = (
        f'<workbook xmlns="{ns}" xmlns:r="{rel_ns}"><sheets>'
        '<sheet name="Data" sheetId="1" r:id="rId1"/>'
        '<sheet name="Extra" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        f'<Relationships xmlns="{pkg_ns}">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    shared = (
        f'<sst xmlns="{ns}"><si><t>Platform</t></si><si><t>Handle</t></si>'
        "<si><t>youtube</t></si><si><t>alice</t></si></sst>"
    )
    sheet1 = (
        f'<worksheet xmlns="{ns}"><sheetData>'
        # 首行全空:表头探测要跳过
        '<row r="1"><c r="A1"/></row>'
        '<row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2" t="s"><v>1</v></c></row>'
        # 数据行:A 共享串,B inlineStr,D 布尔(C 列空洞补位)
        '<row r="3"><c r="A3" t="s"><v>2</v></c>'
        f'<c r="B3" t="inlineStr"><is><t xmlns="{ns}">alice</t></is></c>'
        '<c r="D3" t="b"><v>1</v></c></row>'
        # 全空数据行:跳过
        '<row r="4"><c r="A4"/></row>'
        # 越界共享串索引:容错为空,B 列裸数值
        '<row r="5"><c r="A5" t="s"><v>99</v></c><c r="B5"><v>42</v></c></row>'
        "</sheetData></worksheet>"
    )
    sheet2 = (
        f'<worksheet xmlns="{ns}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>OtherHeader</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>othervalue</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)


class ReadXlsxTests(unittest.TestCase):
    def test_parses_shared_inline_bool_and_skips_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.xlsx"
            _write_minimal_xlsx(path)
            rows = la.read_legacy_rows(path)
            data_rows = [r for r in rows if r["sheet"] == "Data"]
            self.assertEqual(len(data_rows), 2)
            first = data_rows[0]["raw"]
            self.assertEqual(first["Platform"], "youtube")
            self.assertEqual(first["Handle"], "alice")
            self.assertEqual(first["column_4"], "TRUE")
            second = data_rows[1]["raw"]
            self.assertEqual(second["Platform"], "")  # 越界共享串诚实为空
            self.assertEqual(second["Handle"], "42")
            extra_rows = [r for r in rows if r["sheet"] == "Extra"]
            self.assertEqual(len(extra_rows), 1)

    def test_sheet_name_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.xlsx"
            _write_minimal_xlsx(path)
            rows = la.read_legacy_rows(path, sheet_name="Extra")
            self.assertEqual({r["sheet"] for r in rows}, {"Extra"})
            self.assertEqual(rows[0]["raw"], {"OtherHeader": "othervalue"})


class AuditLegacyFileTests(unittest.TestCase):
    def _audit(self, csv_body: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.csv"
            path.write_text(csv_body, encoding="utf-8")
            return la.audit_legacy_file(path)

    def test_end_to_end_issue_and_risk_classification(self):
        body = (
            "Platform,Handle,Email,Product,Amount,Currency\n"
            "youtube,@alice,a@b.com,AF 85mm,\"$1,200.50\",\n"          # 干净行
            "youtube,alice,a@b.com,AF 85mm,\"$1,200.50\",\n"           # 重复 KOL 候选
            ",bob,,,,\n"                                                # 缺平台/联系/产品
            "tiktok,carol,c@d.com,Lens,abc,\n"                          # invalid_amount
            "ig,dave,d@e.com,Lens,-5 usd,\n"                            # negative_amount
            "fb,erin,e@f.com,Lens,500,\n"                               # missing_currency
        )
        out = self._audit(body)
        rows = {r["handle"]: r for r in out["rows"] if r["handle"]}

        clean = rows["alice"]
        self.assertEqual(clean["platform"], "youtube")
        self.assertEqual(clean["amount_value"], 1200.5)
        self.assertEqual(clean["currency"], "USD")
        self.assertIn("duplicate_kol_candidate", clean["issues"])
        self.assertEqual(clean["risk_level"], "medium")  # 仅重复,无高危项
        self.assertTrue(clean["manual_review"])

        bob = rows["bob"]
        self.assertIn("missing_platform", bob["issues"])
        self.assertIn("missing_contact", bob["issues"])
        self.assertIn("missing_product_project", bob["issues"])
        self.assertEqual(bob["risk_level"], "high")
        self.assertEqual(bob["dedup_key"], "")

        self.assertIn("invalid_amount", rows["carol"]["issues"])
        self.assertEqual(rows["carol"]["risk_level"], "high")
        self.assertIn("negative_amount", rows["dave"]["issues"])
        self.assertIn("missing_currency", rows["erin"]["issues"])

        summary = out["summary"]
        self.assertEqual(summary["total_rows"], 6)
        self.assertEqual(summary["recognizable_kol_rows"], 5)
        self.assertEqual(summary["duplicate_groups"], 1)
        self.assertEqual(summary["duplicate_kol_candidates"], 2)
        self.assertEqual(summary["high_risk_rows"], 3)
        self.assertEqual(summary["amount_currency_issue_rows"], 3)
        self.assertEqual(out["duplicate_candidates"], [{"dedup_key": "youtube:alice", "count": 2}])

    def test_url_only_row_backfills_platform_and_handle(self):
        body = (
            "Notes\n"
            "check https://instagram.com/greta for collab\n"
        )
        out = self._audit(body)
        row = out["rows"][0]
        self.assertEqual(row["platform"], "instagram")
        self.assertEqual(row["handle"], "greta")
        self.assertEqual(row["profile_url"], "https://instagram.com/greta")
        self.assertIn("missing_contact", row["issues"])


if __name__ == "__main__":
    unittest.main()
