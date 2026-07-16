"""V-KPI HTML/PDF renderer with local storage."""
from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from jinja2 import Environment

from app.core.logging import get_logger

logger = get_logger(__name__)

REPORT_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{{ title }}</title>
<style>
@page { size: A4; margin: 16mm; @bottom-center { content: "Viltrox Marketing · " counter(page) " / " counter(pages); font-size: 9px; color: #9ca3af; } }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Helvetica", Arial, sans-serif; color:#111827; font-size: 11px; line-height:1.55; }
h1 { font-size: 25px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 20px 0 8px; border-bottom:1px solid #e5e7eb; padding-bottom:5px; }
.header { margin-bottom: 12px; }
.eyebrow { color:#2563eb; letter-spacing:2px; font-size:10px; font-weight:700; text-transform:uppercase; }
.sub { color:#64748b; }
.watermark { color:#94a3b8; font-size:9px; }
.summary { background:#f8fafc; border-left:4px solid #2563eb; padding:10px 12px; border-radius:8px; }
.grid { display: table; width:100%; border-collapse: separate; border-spacing: 8px; margin-left:-8px; }
.cell { display: table-cell; border:1px solid #e5e7eb; border-radius:10px; padding:10px; width:16%; }
.label { color:#64748b; font-size:9px; }
.value { font-size:19px; font-weight:800; margin-top:2px; }
table { width:100%; border-collapse:collapse; margin-top:6px; }
th, td { border:1px solid #e5e7eb; padding:6px 7px; vertical-align:top; }
th { background:#f8fafc; color:#334155; text-align:left; }
td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
.badge { display:inline-block; padding:2px 6px; border-radius:999px; background:#eff6ff; color:#1d4ed8; font-size:9px; }
.alert { padding:7px 9px; border-radius:8px; background:#fff7ed; border:1px solid #fed7aa; margin:5px 0; }
.appendix { margin-top:24px; color:#64748b; font-size:9px; border-top:1px dashed #cbd5e1; padding-top:8px; }
.source-title { margin:12px 0 4px; font-size:12px; font-weight:800; color:#0f172a; }
.source-meta { color:#64748b; font-size:9px; margin-bottom:4px; }
.source-table td { font-size:9px; }
.small { color:#64748b; font-size:9px; }
</style>
</head>
<body>
<div class="header">
  <div class="eyebrow">Viltrox Marketing</div>
  <h1>{{ title }}</h1>
  <div class="sub">周期：{{ period_label }} · 数据来源：真实项目、短链、Shopify/Amazon 归因、成本和 KPI Ledger</div>
  <div class="watermark">员工水印：{{ watermark_user }} · 生成时间：{{ generated_at }} · Report UID：{{ report_uid }}</div>
</div>
<h2>1. 管理层总结</h2>
<div class="summary">{{ summary_text }}</div>
<h2>2. 核心指标</h2>
<div class="grid">
  {% for item in kpis %}<div class="cell"><div class="label">{{ item.label }}</div><div class="value">{{ item.value }}</div><div class="sub">{{ item.note }}</div></div>{% endfor %}
</div>
<h2>3. 项目进度漏斗</h2>
<table><thead><tr><th>阶段</th><th class="num">项目数</th></tr></thead><tbody>{% for row in funnel %}<tr><td>{{ row.stage }}</td><td class="num">{{ row.count }}</td></tr>{% else %}<tr><td colspan="2">当前周期暂无项目进度。</td></tr>{% endfor %}</tbody></table>
<h2>4. 员工贡献</h2>
<table><thead><tr><th>员工</th><th class="num">新增 KOL</th><th class="num">已发布</th><th class="num">本周销售额</th><th class="num">成本</th><th class="num">项目数</th></tr></thead><tbody>{% for row in staff_rows %}<tr><td>{{ row.name }}</td><td class="num">{{ row.kol_claims }}</td><td class="num">{{ row.published }}</td><td class="num">{{ row.sales }}</td><td class="num">{{ row.cost }}</td><td class="num">{{ row.projects }}</td></tr>{% else %}<tr><td colspan="6">暂无员工贡献数据。</td></tr>{% endfor %}</tbody></table>
<h2>5. 项目明细</h2>
<table><thead><tr><th>项目</th><th>KOL</th><th>阶段</th><th>负责人</th><th class="num">销售额</th><th class="num">成本</th><th>更新</th></tr></thead><tbody>{% for row in projects %}<tr><td>{{ row.project_name }}</td><td>{{ row.kol_name }}</td><td><span class="badge">{{ row.stage }}</span></td><td>{{ row.staff_name }}</td><td class="num">{{ row.sales }}</td><td class="num">{{ row.cost }}</td><td>{{ row.updated_at }}</td></tr>{% else %}<tr><td colspan="7">暂无项目明细。</td></tr>{% endfor %}</tbody></table>
<h2>6. 风险与待处理</h2>
{% for row in alerts %}<div class="alert"><strong>{{ row.title }}</strong><br><span>{{ row.description }}</span></div>{% else %}<p class="sub">当前没有未处理提醒。</p>{% endfor %}
<h2>7. 证据口径</h2>
<table><tbody><tr><th>播放量</th><td>来自已抓取内容 / 项目内容统计，不使用假 0 粉丝或假视频数据。</td></tr><tr><th>本周销售额</th><td>来自 Shopify / Amazon / 手动归因表，可下钻到 source_ref 和 evidence_json。</td></tr><tr><th>成本</th><td>发货时自动计入镜头成本；员工只登记快递费和推广费用；void 成本不计入。</td></tr></tbody></table>
<h2>8. Source Rows 附录</h2>
{% for metric in source_appendix %}
<div class="source-title">{{ metric.metric_label }} <span class="small">({{ metric.metric_key }})</span></div>
<div class="source-meta">指标值：{{ metric.value }} · 来源总数：{{ metric.source_count }} · PDF 仅显示前 {{ metric.rows|length }} 条关键来源</div>
<table class="source-table">
  <thead><tr><th>来源</th><th>项目</th><th>KOL</th><th>员工</th><th class="num">贡献</th><th>证据</th><th>发生时间</th><th>快照</th></tr></thead>
  <tbody>
  {% for row in metric.rows %}
    <tr><td>{{ row.source_type }} #{{ row.source_id }}</td><td>{{ row.project }}</td><td>{{ row.kol }}</td><td>{{ row.staff }}</td><td class="num">{{ row.amount }}</td><td>{{ row.evidence }}</td><td>{{ row.occurred_at }}</td><td>{{ row.snapshot }}</td></tr>
  {% else %}
    <tr><td colspan="8">当前指标没有 source rows。若这是新周期，请先运行 KPI/metric lineage。</td></tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="sub">当前 PDF 没有可附加的 source rows。请先生成 metric lineage，或确认当前周期存在真实业务数据。</p>
{% endfor %}
<h2>9. KPI Ledger 附录</h2>
{% if kpi_appendix and kpi_appendix.grouped %}
<table>
  <thead><tr><th>指标</th><th class="num">合计</th><th class="num">来源数</th><th>日期范围</th><th>口径</th></tr></thead>
  <tbody>
  {% for row in kpi_appendix.grouped %}
    <tr><td>{{ row.metric_label }} <span class="small">({{ row.metric_key }})</span></td><td class="num">{{ row.formatted_total }}</td><td class="num">{{ row.source_count }}</td><td>{{ row.first_date }} → {{ row.last_date }}</td><td>{{ '推荐 outcome，不重复计入主财务' if row.is_recommendation_metric else row.confidence }}</td></tr>
  {% endfor %}
  </tbody>
</table>
<div class="source-title">KPI Source Rows <span class="small">显示前 {{ kpi_appendix.source_rows|length }} 条</span></div>
<table class="source-table">
  <thead><tr><th>指标</th><th>项目</th><th>KOL</th><th>员工</th><th class="num">值</th><th>来源</th><th>公式 / 推荐</th><th>组件</th></tr></thead>
  <tbody>
  {% for row in kpi_appendix.source_rows %}
    <tr>
      <td>{{ row.metric_label }}</td>
      <td>{{ row.project }}</td>
      <td>{{ row.kol }}</td>
      <td>{{ row.staff }}</td>
      <td class="num">{{ row.value }}</td>
      <td>{{ row.source_type }} · {{ row.source_ref }}</td>
      <td>{% if row.formula %}{{ row.formula }}{% elif row.recommendation_id %}Rec #{{ row.recommendation_id }} / Outcome #{{ row.outcome_id }} / Launch #{{ row.launch_id }}{% else %}{{ row.confidence }}{% endif %}</td>
      <td>{{ row.component_summary or '-' }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="sub">当前周期暂无 KPI Ledger source rows。需要先运行 KPI rollup。</p>
{% endif %}
<div class="appendix">Metric Run：{{ metric_run_id or '-' }} · 文件包含员工水印和导出审计记录。</div>
</body>
</html>"""

_TEMPLATE_ENV = Environment(autoescape=True)
_REPORT_TEMPLATE = _TEMPLATE_ENV.from_string(REPORT_HTML_TEMPLATE)


def configured_report_storage_path() -> Path:
    """Return the configured absolute storage path without touching disk."""
    raw = os.environ.get("VKPI_REPORT_STORAGE_PATH") or "runtime/vkpi-reports"
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def report_storage_dir() -> Path:
    path = configured_report_storage_path()
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_report_html(context: dict[str, Any]) -> str:
    return _REPORT_TEMPLATE.render(**context)


def _deny_report_resource(url: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Reports are self-contained; fail closed on file, network, or data URLs."""
    del _args, _kwargs
    raise ValueError(f"report resource loading is disabled: {str(url).split(':', 1)[0] or 'relative'}")


def render_pdf_bytes(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("WeasyPrint is not installed or system libraries are missing") from exc
    return HTML(string=html, url_fetcher=_deny_report_resource).write_pdf()


def _safe_filename(filename: str) -> str:
    clean = str(filename or "").strip()
    if not clean or clean in {".", ".."}:
        raise ValueError("report filename is required")
    path = Path(clean)
    if path.is_absolute() or path.name != clean or "/" in clean or "\\" in clean:
        raise ValueError("report filename must not contain a path")
    return clean


def _direct_stored_path(file_path: str | Path) -> tuple[Path, Path]:
    """Return the canonical storage root and a lexical direct child.

    The final component is deliberately *not* resolved: resolving it would
    follow a symlink before the caller has a chance to reject it.  Resolving
    only the parent also lets configured storage paths use an absolute alias
    while still requiring the DB value to name exactly one direct child.
    """
    raw = str(file_path or "").strip()
    candidate = Path(raw)
    if not raw or not candidate.is_absolute():
        raise ValueError("stored report path must be absolute")
    if candidate.name in {"", ".", ".."} or ".." in candidate.parts:
        raise ValueError("stored report path must be a direct child")

    # Reads/removals must never create a missing storage directory.  Only the
    # publication path (report_storage_dir/store_bytes) is allowed to mkdir.
    configured_root = configured_report_storage_path()
    lexical_root = Path(os.path.abspath(str(configured_root)))
    lexical_parent = Path(os.path.abspath(str(candidate.parent)))
    if lexical_parent != lexical_root:
        raise ValueError("stored report path is outside report storage or not a direct child")

    root = configured_root.resolve(strict=True)
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError("stored report path parent is unavailable") from exc
    if parent != root:
        raise ValueError("stored report path is outside report storage or not a direct child")
    return root, root / candidate.name


def resolve_stored_path(file_path: str | Path) -> Path:
    """Validate an existing regular, non-symlink direct storage child.

    This helper is suitable for immediate local maintenance operations.  HTTP
    downloads must use :func:`open_stored_file`, which removes the remaining
    lstat/open time-of-check/time-of-use window by validating the opened fd.
    """
    _root, candidate = _direct_stored_path(file_path)
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise ValueError("stored report file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ValueError("stored report file must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("stored report file must be regular")
    return candidate


@dataclass
class OpenedStoredFile:
    """An already validated report file whose descriptor owns the bytes read."""

    path: Path
    size: int
    _handle: BinaryIO = field(repr=False)

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def close(self) -> None:
        # IOBase.close() is idempotent, which is important because both the
        # iterator and the response-level cancellation cleanup call this.
        self._handle.close()

    def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        try:
            while True:
                chunk = self._handle.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            self.close()


def verify_opened_file(
    opened: OpenedStoredFile,
    *,
    expected_size: Any = None,
    expected_sha256: str = "",
) -> None:
    """Verify stored bytes before an HTTP response or destructive cleanup.

    The descriptor remains the authority throughout verification, so replacing
    the pathname cannot redirect either the digest calculation or the later
    stream.  Legacy rows may omit size/SHA metadata; in that case the regular
    file + no-follow checks performed by :func:`open_stored_file` remain the
    fail-closed boundary available without a schema migration.
    """
    size: int | None
    if expected_size in (None, ""):
        size = None
    else:
        try:
            size = int(expected_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("stored report size metadata is invalid") from exc
        if size < 0:
            raise ValueError("stored report size metadata is invalid")
    if size is not None and opened.size != size:
        raise ValueError("stored report size mismatch")

    digest = str(expected_sha256 or "").strip().lower()
    if not digest:
        return
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("stored report digest metadata is invalid")

    try:
        opened._handle.seek(0)
        hasher = hashlib.sha256()
        while True:
            chunk = opened._handle.read(64 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise ValueError("stored report digest mismatch")
    finally:
        # A successful validation must leave the descriptor ready to stream;
        # a failed validation is closed by the caller's exception path.
        opened._handle.seek(0)


def open_stored_file(file_path: str | Path) -> OpenedStoredFile:
    """Open one storage child without following links and validate its fd.

    Opening relative to an already-open directory descriptor prevents path
    replacement after validation from redirecting the download.  ``O_NONBLOCK``
    also prevents a swapped FIFO from hanging the request before ``fstat`` can
    reject it.
    """
    root, candidate = _direct_stored_path(file_path)
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:  # pragma: no cover - supported deployment platforms
        raise OSError("secure no-follow file opening is unavailable")

    common_flags = getattr(os, "O_CLOEXEC", 0) | no_follow
    directory_fd = os.open(
        str(root),
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | common_flags,
    )
    file_fd: int | None = None
    try:
        file_fd = os.open(
            candidate.name,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | common_flags,
            dir_fd=directory_fd,
        )
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("stored report file must be regular")
        handle = os.fdopen(file_fd, "rb", closefd=True)
        file_fd = None
        return OpenedStoredFile(path=candidate, size=info.st_size, _handle=handle)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes after publishing or removing a report."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(str(path), flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _remove_published_path_if_owned(path: Path, *, device: int, inode: int) -> None:
    """Best-effort rollback without unlinking a path another writer replaced."""
    try:
        current = path.lstat()
        if current.st_dev != device or current.st_ino != inode:
            return
        path.unlink()
        _fsync_directory(path.parent)
    except FileNotFoundError:
        return


def remove_stored_file(
    file_path: str | Path,
    *,
    expected_size: Any = None,
    expected_sha256: str = "",
) -> bool:
    """Remove one validated direct child and durably persist the unlink.

    The opened descriptor and the final lstat must identify the same inode.
    This prevents a stale cleanup attempt from deleting a path that was already
    replaced before validation.  The storage directory is application-owned;
    callers must still serialize same-name writers because POSIX has no atomic
    unlink-if-inode-matches primitive.
    """
    opened = open_stored_file(file_path)
    try:
        verify_opened_file(
            opened,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        descriptor_info = os.fstat(opened._handle.fileno())
        current = opened.path.lstat()
        if (
            current.st_dev != descriptor_info.st_dev
            or current.st_ino != descriptor_info.st_ino
            or not stat.S_ISREG(current.st_mode)
        ):
            raise ValueError("stored report path changed during cleanup")
        opened.path.unlink()
        _fsync_directory(opened.path.parent)
        return True
    except FileNotFoundError:
        return False
    finally:
        opened.close()


def store_bytes(content: bytes, *, filename: str) -> dict[str, Any]:
    root = report_storage_dir()
    path = root / _safe_filename(filename)
    digest = hashlib.sha256(content).hexdigest()
    fd, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(root),
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        # Validate the staged bytes before making the filename visible.  This
        # catches short writes/corruption without ever exposing a partial report.
        staged_size = temp_path.stat().st_size
        staged_digest = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        if staged_size != len(content) or staged_digest != digest:
            raise OSError("staged report validation failed")

        # link(2) is an atomic no-clobber publish on the same filesystem:
        # exactly one concurrent writer can create the final directory entry.
        # Unlike replace(), an existing file (including a broken symlink) raises
        # EEXIST instead of being overwritten.
        staged_stat = temp_path.stat()
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise FileExistsError(f"report file already exists: {path.name}") from exc
        try:
            # Remove the staging name before the durability barrier so this one
            # directory fsync persists both creation of the final name and
            # deletion of the temporary name.  The final hard link continues
            # to own the already-fsynced bytes.
            temp_path.unlink()
            _fsync_directory(root)
        except Exception:
            _remove_published_path_if_owned(
                path,
                device=staged_stat.st_dev,
                inode=staged_stat.st_ino,
            )
            raise
        return {
            "file_path": str(path),
            "file_size_bytes": staged_size,
            "sha256_hex": staged_digest,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    finally:
        # On success the final hard link owns the bytes; this removes only this
        # invocation's uniquely named staging link.  On failure it removes the
        # unpublished temporary file.
        temp_path.unlink(missing_ok=True)


def render_and_store_pdf(context: dict[str, Any], *, filename: str) -> dict[str, Any]:
    html = render_report_html(context)
    pdf = render_pdf_bytes(html)
    return {**store_bytes(pdf, filename=filename), "html": html}
