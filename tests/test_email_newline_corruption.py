"""复现 + 锁定:raw_full_scan 换行腐蚀 bug(业务活雷:外联往错地址发)。

根因:extract_contacts_multi_source 的全 raw 兜底扫描先 json.dumps(raw) 再跑
_EMAIL_RE。json.dumps 把真实换行/制表符转义成字面两字符序列(\\n、\\r、\\t),
而邮箱正则的本地部分字符类含字母不含反斜杠,于是「换行+邮箱」被吞成
n 前缀假地址(\nfoo@bar.com -> nfoo@bar.com),\t 同理吞成 t 前缀。

本文件同时是修复后的回归锁:兜底扫描必须扫原始字符串叶子,不扫转义文本。
"""
from __future__ import annotations

from app.domains.kol.business_contact_extract import extract_contacts_multi_source


def _emails(contacts: list[dict]) -> dict[str, str]:
    """{contact_value: source_type},只取 email 类。"""
    return {
        c["contact_value"]: c["source_type"]
        for c in contacts
        if c["contact_type"] == "email"
    }


def _full_scan_raw(text: str) -> dict:
    """把文案埋进 _text_blobs/_author_nested_blobs 都够不到的嵌套位置,
    确保命中只能来自 raw_full_scan 兜底扫描。"""
    return {"latestPosts": [{"text": text}]}


def test_lf_before_email_not_swallowed_into_n_prefix() -> None:
    contacts = extract_contacts_multi_source(
        _full_scan_raw("business inquiries below\nfoo@bar.com"), platform="instagram"
    )
    emails = _emails(contacts)
    assert "foo@bar.com" in emails
    assert emails["foo@bar.com"] == "raw_full_scan"
    assert "nfoo@bar.com" not in emails


def test_crlf_before_email_not_swallowed() -> None:
    contacts = extract_contacts_multi_source(
        _full_scan_raw("contact:\r\nfoo@bar.com for collabs"), platform="youtube"
    )
    emails = _emails(contacts)
    assert "foo@bar.com" in emails
    assert "nfoo@bar.com" not in emails
    assert "rnfoo@bar.com" not in emails


def test_tab_before_email_not_swallowed() -> None:
    contacts = extract_contacts_multi_source(
        _full_scan_raw("mail me\tgaffer@studio.org"), platform="tiktok"
    )
    emails = _emails(contacts)
    assert "gaffer@studio.org" in emails
    assert "tgaffer@studio.org" not in emails


def test_multiline_multi_email_all_clean() -> None:
    text = (
        "About my channel\n"
        "business: alice@lenswork.com\n"
        "backup contact\r\n"
        "bob.smith@example-agency.net\n"
        "personal\tcarol_99@filmcrew.studio\n"
    )
    contacts = extract_contacts_multi_source(_full_scan_raw(text), platform="youtube")
    emails = _emails(contacts)
    assert "alice@lenswork.com" in emails
    assert "bob.smith@example-agency.net" in emails
    assert "carol_99@filmcrew.studio" in emails
    for value in emails:
        assert not value.startswith(("nalice", "nbob", "ncarol", "tcarol", "rnbob"))


def test_email_legitimately_starting_with_n_is_preserved() -> None:
    """真 n 开头的邮箱不许被误伤(修复只该去伪前缀,不该剥真字符)。"""
    contacts = extract_contacts_multi_source(
        _full_scan_raw("reach me\nnina@bar.com"), platform="instagram"
    )
    emails = _emails(contacts)
    assert "nina@bar.com" in emails
    assert "ina@bar.com" not in emails
    assert "nnina@bar.com" not in emails


def test_deeply_nested_strings_still_scanned() -> None:
    """修复不许缩小覆盖面:任意深度嵌套字符串叶子仍要被兜底扫到。"""
    raw = {
        "items": [
            {
                "meta": {
                    "cards": [
                        {"blurb": "for business\ndeep@nested.info"},
                    ]
                }
            }
        ]
    }
    contacts = extract_contacts_multi_source(raw, platform="instagram")
    emails = _emails(contacts)
    assert "deep@nested.info" in emails
    assert "ndeep@nested.info" not in emails


def test_structured_field_extraction_unchanged() -> None:
    """特征锁:结构化字段抽取行为原样(高置信路径不受兜底扫描修复影响)。"""
    raw = {
        "profile": {
            "public_email": "biz@creator.com",
            "biography": "business inquiries: bio@creator.com",
        }
    }
    contacts = extract_contacts_multi_source(raw, platform="instagram")
    emails = _emails(contacts)
    assert emails.get("biz@creator.com") == "ig_business_profile"
    assert emails.get("bio@creator.com") == "raw_bio_scan"
