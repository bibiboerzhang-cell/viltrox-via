"""合同生成器 v1(2026-06-12 施工:模板为骨,LLM 只润色不碰本体)。

确定性填槽:同输入=同输出;正文逐字来自 contract_templates(法务冻结)。
产出 DOCX(标准库 zipfile 手写 OOXML,零新依赖——闸 B 不开;PDF 直出候装库报备)。
落档走既有 create_contract_from_file 链(R2 + vkpi_project_contracts + 归档列表),
文件名前缀 GEN- 标记生成来源。
"""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from app.core.logging import get_logger
from app.domains.projects.contract_templates import TEMPLATES, template_catalog

logger = get_logger("viltrox.domains.projects.contract_generator")

_SLOT_RE = re.compile(r"\[\[([a-z0-9_]+)\]\]")
_BLANK = "____________________"

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _para(text: str, *, bold: bool = False, size: int = 22, space_after: int = 120) -> str:
    """单段 OOXML。size 单位 half-point(22=11pt)。"""
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
    return (
        f'<w:p><w:pPr><w:spacing w:after="{space_after}"/></w:pPr>'
        f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
    )


def _docx_bytes(sections: list[tuple[str, str]]) -> bytes:
    body: list[str] = []
    for kind, text in sections:
        if kind == "h1":
            body.append(_para(text, bold=True, size=30, space_after=240))
        elif kind == "h2":
            body.append(_para(text, bold=True, size=24, space_after=160))
        elif kind == "li":
            body.append(_para(f"• {text}", space_after=60))
        else:
            body.append(_para(text))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        "</w:body></w:document>"
    )
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _pdf_bytes(sections: list[tuple[str, str]]) -> bytes:
    """reportlab 直出 PDF(2026-06-12 批E,闸B 报备=A-F 字):STSong CID 字体兼容中英,
    PDF 落档自动触发 Claude 提取回读(create_contract_from_file 既有链)= spec 的自校验。"""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font = "STSong-Light"
    except Exception:
        font = "Helvetica"
    styles = {
        "h1": ParagraphStyle("h1", fontName=font, fontSize=15, leading=20, spaceAfter=12),
        "h2": ParagraphStyle("h2", fontName=font, fontSize=12, leading=16, spaceAfter=8, spaceBefore=6),
        "p": ParagraphStyle("p", fontName=font, fontSize=10, leading=14, spaceAfter=6),
        "li": ParagraphStyle("li", fontName=font, fontSize=10, leading=14, spaceAfter=3, leftIndent=12, bulletIndent=4),
    }
    flow = []
    for kind, text in sections:
        safe = escape(text)
        if kind == "li":
            flow.append(Paragraph(safe, styles["li"], bulletText="•"))
        else:
            flow.append(Paragraph(safe, styles.get(kind, styles["p"])))
    flow.append(Spacer(1, 6 * mm))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm).build(flow)
    return buffer.getvalue()


def _render_choice(slot: dict[str, Any], chosen: str) -> str:
    """choice 槽按原模板的勾选框形式渲染:☑ 选中 ☐ 未选。"""
    parts = []
    for option in slot.get("options") or []:
        mark = "☑" if option == chosen else "☐"
        parts.append(f"{mark} {option}")
    if chosen and chosen not in (slot.get("options") or []):
        parts.append(f"☑ {chosen}")
    return "   ".join(parts) if parts else (chosen or _BLANK)


def list_templates() -> dict[str, Any]:
    return template_catalog()


# 批B #7(2026-06-12):GEN 合同费回填用的槽位映射——paid: campaign_fee / event: payment_usd(USD);
# exchange 模板无现金费,fee 槽缺失即不回填 fee_amount。
_FEE_SLOT_KEYS = ("campaign_fee", "payment_usd")
_DURATION_SLOT_KEYS = ("contract_duration", "delivery_days", "duration")
_AMOUNT_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)")
_CURRENCY_RE = re.compile(r"\b(USD|EUR|GBP|CNY|RMB|JPY|AUD|CAD|HKD)\b", re.I)


def _generated_fee(fields: dict[str, Any]) -> tuple[float | None, str | None]:
    """从填槽值确定性解析签约费;解析不出 → (None, None),绝不猜。"""
    for key in _FEE_SLOT_KEYS:
        raw = str(fields.get(key) or "").strip()
        if not raw:
            continue
        match = _AMOUNT_RE.search(raw.replace(" ", ""))
        if not match:
            continue
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if amount <= 0:
            continue
        currency = "USD"
        cur = _CURRENCY_RE.search(raw)
        if cur:
            code = cur.group(1).upper()
            currency = "CNY" if code == "RMB" else code
        return amount, currency
    return None, None


def _generated_deliverables(template: dict[str, Any], fields: dict[str, Any]) -> list[str]:
    """交付组(group='交付')已填槽 → 'label: value' 列表,跨三模板通用且确定性。"""
    items: list[str] = []
    for slot in template["slots"]:
        if str(slot.get("group") or "") != "交付":
            continue
        value = str(fields.get(slot["key"]) or "").strip()
        if value:
            items.append(f"{slot['label']}: {value}")
    return items


def _backfill_generated_contract_fields(
    project_id: int,
    contract_id: int,
    *,
    template_key: str,
    template: dict[str, Any],
    fields: dict[str, Any],
) -> None:
    """落档后用槽值回填 vkpi_project_contracts 结构化列(批B #7)。

    GEN docx 不走提取(skipped),回填是其唯一结构化来源;GEN pdf 的异步提取回读
    之后会覆盖(spec 自校验),回填保证落档当刻即有费用/交付物可用。
    source='generator' 记入 raw_extracted_json 留痕。
    """
    from app.db.connection import get_conn

    fee_amount, fee_currency = _generated_fee(fields)
    deliverables = _generated_deliverables(template, fields)
    duration = ""
    for key in _DURATION_SLOT_KEYS:
        if str(fields.get(key) or "").strip():
            duration = str(fields.get(key)).strip()
            break
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trace = {
        "source": "generator",
        "template_key": template_key,
        "fields": {key: value for key, value in (fields or {}).items() if str(value or "").strip()},
        "generated_at": now,
    }
    updates = [
        "contract_duration=?",
        "deliverables_json=?::jsonb",
        "deliverable_count=?",
        "raw_extracted_json=?::jsonb",
        "updated_at=?",
    ]
    params: list[Any] = [
        duration,
        json.dumps(deliverables, ensure_ascii=False),
        len(deliverables) or None,
        json.dumps(trace, ensure_ascii=False, default=str),
        now,
    ]
    if fee_amount is not None:
        updates = ["fee_amount=?", "fee_currency=?", *updates]
        params = [fee_amount, fee_currency or "USD", *params]
    params.extend([int(contract_id), int(project_id)])
    conn = get_conn()
    conn.execute(
        f"UPDATE vkpi_project_contracts SET {', '.join(updates)} WHERE id=? AND project_id=?",
        tuple(params),
    )
    conn.commit()


def generate_contract(
    project_id: int,
    *,
    template_key: str,
    fields: dict[str, Any],
    assignment_id: int | None = None,
    kol_pool_id: int | None = None,
    staff: dict[str, Any] | None = None,
    output_format: str = "pdf",
) -> dict[str, Any]:
    """填槽生成 DOCX 并落档(R2+合同表)。required 缺失报 400 语义的 ValueError。"""
    template = TEMPLATES.get(str(template_key or "").strip())
    if not template:
        raise LookupError(f"unknown contract template: {template_key}")
    slots = {slot["key"]: slot for slot in template["slots"]}
    missing = [
        slot["key"]
        for slot in template["slots"]
        if slot.get("required") and not str(fields.get(slot["key"]) or "").strip()
    ]
    if missing:
        raise ValueError("缺少必填字段: " + ", ".join(missing))

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        slot = slots.get(key) or {}
        raw = str(fields.get(key) or "").strip()
        if slot.get("type") == "choice":
            return _render_choice(slot, raw)
        return raw or _BLANK

    rendered = [(kind, _SLOT_RE.sub(fill, text)) for kind, text in template["sections"]]
    fmt = "pdf" if str(output_format or "pdf").lower() == "pdf" else "docx"
    if fmt == "pdf":
        try:
            payload = _pdf_bytes(rendered)
        except Exception:
            logger.warning("pdf render failed, falling back to docx")
            fmt = "docx"
            payload = _docx_bytes(rendered)
    else:
        payload = _docx_bytes(rendered)

    party_b = re.sub(r"[^A-Za-z0-9._-]+", "-", str(fields.get("party_b_name") or "creator")).strip("-")[:40]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"GEN-{template_key}-{party_b}-{stamp}.{fmt}"

    from app.domains.projects.contracts import create_contract_from_file

    with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as handle:
        handle.write(payload)
        tmp_path = handle.name
    try:
        result = create_contract_from_file(
            int(project_id),
            tmp_path,
            file_name=file_name,
            mime_type="application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            assignment_id=assignment_id,
            kol_pool_id=kol_pool_id,
            staff=staff,
        )
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("temp contract file cleanup failed: %s", tmp_path)
    # 批B #7:落档后用槽值回填结构化列(fee/duration/deliverables),source='generator' 留痕
    contract_row = result.get("contract") if isinstance(result.get("contract"), dict) else {}
    contract_row_id = int(contract_row.get("id") or 0)
    if contract_row_id:
        _backfill_generated_contract_fields(
            int(project_id),
            contract_row_id,
            template_key=str(template_key),
            template=template,
            fields=fields or {},
        )
        from app.domains.projects.contracts import get_contract

        result["contract"] = get_contract(int(project_id), contract_row_id, staff=staff)
    result["generated"] = True
    result["template_key"] = template_key
    result["file_name"] = file_name
    return result
