"""Small dependency-free XLSX reader for import dry-runs.

The backend runtime does not require openpyxl. This reader only extracts cell
values from normal worksheet XML and is intentionally read-only.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@dataclass(frozen=True)
class SheetRows:
    name: str
    rows: list[dict[str, Any]]


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return max(index - 1, 0)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(f"{NS_MAIN}si"):
        parts = [node.text or "" for node in item.findall(f".//{NS_MAIN}t")]
        values.append("".join(parts))
    return values


def _sheet_paths(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_by_id: dict[str, str] = {}
    for rel in rels.findall(f"{NS_PACKAGE_REL}Relationship"):
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "").lstrip("/")
        if not target:
            continue
        rel_by_id[rel_id] = target if target.startswith("xl/") else f"xl/{target}"

    sheet_map: dict[str, str] = {}
    for sheet in workbook.findall(f".//{NS_MAIN}sheet"):
        name = sheet.attrib.get("name", "")
        rel_id = sheet.attrib.get(f"{NS_REL}id", "")
        path = rel_by_id.get(rel_id, "")
        if name and path:
            sheet_map[name] = path
    return sheet_map


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{NS_MAIN}t")).strip()

    value_node = cell.find(f"{NS_MAIN}v")
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text
    if cell_type == "s":
        try:
            return shared[int(raw)].strip()
        except (IndexError, ValueError):
            return ""
    return raw.strip()


def _read_matrix(archive: zipfile.ZipFile, path: str, shared: list[str]) -> list[list[Any]]:
    root = ElementTree.fromstring(archive.read(path))
    matrix: list[list[Any]] = []
    for row in root.findall(f".//{NS_MAIN}row"):
        cells: dict[int, Any] = {}
        for cell in row.findall(f"{NS_MAIN}c"):
            ref = cell.attrib.get("r", "")
            cells[_column_index(ref)] = _cell_value(cell, shared)
        if not cells:
            matrix.append([])
            continue
        max_index = max(cells)
        matrix.append([cells.get(index, "") for index in range(max_index + 1)])
    return matrix


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _header_score(row: list[Any], expected_tokens: set[str]) -> int:
    return sum(1 for value in row if _text(value) in expected_tokens)


def rows_from_sheet(path: str | Path, sheet_name: str, *, expected_headers: set[str]) -> SheetRows:
    xlsx_path = Path(path)
    with zipfile.ZipFile(xlsx_path) as archive:
        shared = _shared_strings(archive)
        sheet_paths = _sheet_paths(archive)
        sheet_path = sheet_paths.get(sheet_name)
        if not sheet_path:
            raise KeyError(f"sheet not found: {sheet_name}")
        matrix = _read_matrix(archive, sheet_path, shared)

    header_index = 0
    best_score = -1
    for index, row in enumerate(matrix[:30]):
        score = _header_score(row, expected_headers)
        if score > best_score:
            best_score = score
            header_index = index

    headers = [_text(value) for value in matrix[header_index]]
    rows: list[dict[str, Any]] = []
    for row_number, values in enumerate(matrix[header_index + 1 :], start=header_index + 2):
        row = {
            headers[index]: values[index] if index < len(values) else ""
            for index in range(len(headers))
            if headers[index]
        }
        if any(_text(value) for value in row.values()):
            row["__row_number"] = row_number
            rows.append(row)
    return SheetRows(name=sheet_name, rows=rows)

