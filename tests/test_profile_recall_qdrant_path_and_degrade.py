"""Qdrant 路径可由 env 迁移;索引打不开时降级原因必须是 qdrant_index_unavailable 而非 embedding_unavailable;
worker 单元模板必须给 runtime/vkpi_qdrant 可写(2026-08-23 prod 严格 30 搜索 0/30 根因)。"""
from __future__ import annotations

import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_qdrant_path_env_precedence(monkeypatch, tmp_path):
    from app.domains.kol import profile_recall_contract as c

    monkeypatch.setenv("VKPI_KOL_QDRANT_PATH", str(tmp_path / "q"))
    assert c._qdrant_local_path() == tmp_path / "q"
    monkeypatch.delenv("VKPI_KOL_QDRANT_PATH")
    monkeypatch.setenv("VKPI_RUNTIME_DATA_DIR", str(tmp_path / "root"))
    assert c._qdrant_local_path() == tmp_path / "root" / "runtime" / "vkpi_qdrant"
    monkeypatch.delenv("VKPI_RUNTIME_DATA_DIR")
    assert c._qdrant_local_path() == c.PROJECT_ROOT / "runtime" / "vkpi_qdrant"


def test_worker_units_allow_writing_qdrant_lock():
    for name in ("vkpi-worker-bulk@.service", "vkpi-worker-interactive.service"):
        text = (ROOT / "scripts/ops/systemd" / name).read_text(encoding="utf-8")
        assert "ReadWritePaths=/opt/viltrox-2.0/runtime/vkpi_qdrant" in text, name


def test_readonly_index_degrades_as_qdrant_not_embedding():
    from app.domains.kol import profile_recall_support as sup

    assert sup.classify_recall_failure(OSError(30, "Read-only file system: '/opt/x/runtime/vkpi_qdrant/.lock'")) == "qdrant_index_unavailable"
    assert sup.classify_recall_failure(RuntimeError("qdrant_local_path_missing:/x")) == "qdrant_index_unavailable"
    assert sup.classify_recall_failure(TimeoutError("deadline exceeded")) == "embedding_timeout"
    assert sup.classify_recall_failure(RuntimeError("openai 401")) == "embedding_unavailable"
    src = Path(importlib.import_module("app.domains.kol.profile_recall").__file__).read_text(encoding="utf-8")
    assert "_support.classify_recall_failure(exc)" in src
