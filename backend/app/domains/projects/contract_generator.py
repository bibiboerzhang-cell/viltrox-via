"""合同生成器 v1(2026-06-12 施工:模板为骨,LLM 只润色不碰本体)。

确定性填槽:同输入=同输出;正文逐字来自 contract_templates(法务冻结)。
产出 DOCX(标准库 zipfile 手写 OOXML,零新依赖——闸 B 不开;PDF 直出候装库报备)。
落档走既有 create_contract_from_file 链(R2 + vkpi_project_contracts + 归档列表),
文件名前缀 GEN- 标记生成来源。
"""
from __future__ import annotations

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


def generate_contract(
    project_id: int,
    *,
    template_key: str,
    fields: dict[str, Any],
    assignment_id: int | None = None,
    kol_pool_id: int | None = None,
    staff: dict[str, Any] | None = None,
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
    payload = _docx_bytes(rendered)

    party_b = re.sub(r"[^A-Za-z0-9._-]+", "-", str(fields.get("party_b_name") or "creator")).strip("-")[:40]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"GEN-{template_key}-{party_b}-{stamp}.docx"

    from app.domains.projects.contracts import create_contract_from_file

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
        handle.write(payload)
        tmp_path = handle.name
    try:
        result = create_contract_from_file(
            int(project_id),
            tmp_path,
            file_name=file_name,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            assignment_id=assignment_id,
            kol_pool_id=kol_pool_id,
            staff=staff,
        )
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("temp contract file cleanup failed: %s", tmp_path)
    result["generated"] = True
    result["template_key"] = template_key
    result["file_name"] = file_name
    return result
