"""embedding 403 failover + 维度守卫单测(挂账迸发④ 2026-07-12)。

根因:OpenAI embeddings 经 Decodo sticky 端口(出口 IP 被钉住)被 Cloudflare 按 IP 信誉
403;修法=同凭据换端口换出口 failover,供应商/向量空间零变更。本文件冻结:
① 出口轮换选路 ② 403/连接类才轮换、其余立刻上抛 ③ 直连兜底 gate 默认 OFF
④ 模型↔collection↔维度配对(换模型必须开新 collection,绝不混维度写同 collection)。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.domains.kol import profile_recall  # noqa: E402


# ── 配对冻结:换 embedding 模型必须开平行 collection,谁改这行谁负责重建索引 ──


def test_model_collection_dim_pairing_frozen():
    assert profile_recall.COLLECTION_NAME == "vkpi_kol_profile_index_v1"
    assert profile_recall.EMBEDDING_MODEL == "text-embedding-3-small"
    assert profile_recall.VECTOR_SIZE == 1536


# ── 出口轮换选路 ──────────────────────────────────────────────────────────


def test_rotation_candidates_default_ports(monkeypatch):
    monkeypatch.delenv(profile_recall.EMBED_PROXY_ROTATE_PORTS_ENV, raising=False)
    out = profile_recall._proxy_rotation_candidates("http://user:pw@gate.example.com:10001")
    assert out == [
        "http://user:pw@gate.example.com:10002",
        "http://user:pw@gate.example.com:7000",
    ]


def test_rotation_candidates_skip_current_port_and_env_override(monkeypatch):
    monkeypatch.setenv(profile_recall.EMBED_PROXY_ROTATE_PORTS_ENV, "10001, 10005,junk,10005")
    out = profile_recall._proxy_rotation_candidates("http://u:p@gate.example.com:10001")
    # 跳过与当前相同的 10001、非数字 token、重复项
    assert out == ["http://u:p@gate.example.com:10005"]


def test_rotation_candidates_portless_proxy_returns_empty():
    assert profile_recall._proxy_rotation_candidates("") == []
    assert profile_recall._proxy_rotation_candidates("socks5://gate.example.com") == []


def test_transport_plan_direct_gate_default_off(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://u:p@gate.example.com:10001")
    monkeypatch.delenv(profile_recall.EMBED_DIRECT_FALLBACK_ENV, raising=False)
    plan = profile_recall._embed_transport_plan()
    labels = [p["transport"] for p in plan]
    assert labels == ["proxy_primary", "proxy_rotated:10002", "proxy_rotated:7000"]
    assert all(not p["direct"] for p in plan)


def test_transport_plan_direct_gate_on(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://u:p@gate.example.com:10001")
    monkeypatch.setenv(profile_recall.EMBED_DIRECT_FALLBACK_ENV, "1")
    plan = profile_recall._embed_transport_plan()
    assert [p["transport"] for p in plan][-1] == "direct"
    assert plan[-1]["direct"] is True


def test_transport_plan_no_proxy_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "")
    monkeypatch.setenv("YTDLP_PROXY", "")
    monkeypatch.delenv(profile_recall.EMBED_DIRECT_FALLBACK_ENV, raising=False)
    plan = profile_recall._embed_transport_plan()
    assert [p["transport"] for p in plan] == ["proxy_primary"]  # 行为同旧:单次尝试


# ── failover 判据 ─────────────────────────────────────────────────────────


class _Boom403(Exception):
    status_code = 403


class _Boom401(Exception):
    status_code = 401


class APIConnectionError(Exception):  # 类名即判据(鸭子类型)
    pass


def test_should_failover_matrix():
    assert profile_recall._should_failover(_Boom403()) is True
    assert profile_recall._should_failover(APIConnectionError()) is True
    assert profile_recall._should_failover(_Boom401()) is False
    assert profile_recall._should_failover(ValueError("x")) is False


# ── failover 执行:403 换出口成功 / 非 403 立刻上抛 ───────────────────────


class _FakeResp:
    def __init__(self, dim: int):
        emb = types.SimpleNamespace(embedding=[0.0] * dim)
        self.data = [emb]
        self.usage = types.SimpleNamespace(prompt_tokens=5, total_tokens=5)


def _factory_seq(*behaviors):
    """behaviors: 异常实例=该次尝试抛;其他=作为 resp 返回。记录用到的 transport。"""
    calls: list[str] = []
    seq = list(behaviors)

    def factory(spec):
        calls.append(spec["transport"])
        behavior = seq.pop(0)

        class _Client:
            class embeddings:  # noqa: N801 — 模仿 openai client 形状
                @staticmethod
                def create(**kwargs):
                    if isinstance(behavior, Exception):
                        raise behavior
                    return behavior

        return _Client()

    return factory, calls


def test_failover_403_rotates_to_next_exit(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://u:p@gate.example.com:10001")
    monkeypatch.delenv(profile_recall.EMBED_DIRECT_FALLBACK_ENV, raising=False)
    factory, calls = _factory_seq(_Boom403(), _FakeResp(profile_recall.VECTOR_SIZE))
    resp, transport = profile_recall._create_embedding_with_failover("q", client_factory=factory)
    assert transport == "proxy_rotated:10002"
    assert calls == ["proxy_primary", "proxy_rotated:10002"]
    assert len(resp.data[0].embedding) == profile_recall.VECTOR_SIZE


def test_failover_non_403_raises_immediately(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://u:p@gate.example.com:10001")
    factory, calls = _factory_seq(_Boom401())
    with pytest.raises(_Boom401):
        profile_recall._create_embedding_with_failover("q", client_factory=factory)
    assert calls == ["proxy_primary"]  # 不轮换


def test_failover_all_exits_blocked_raises_last(monkeypatch):
    monkeypatch.setenv("OPENAI_PROXY", "http://u:p@gate.example.com:10001")
    monkeypatch.delenv(profile_recall.EMBED_DIRECT_FALLBACK_ENV, raising=False)
    factory, calls = _factory_seq(_Boom403(), _Boom403(), _Boom403())
    with pytest.raises(_Boom403):
        profile_recall._create_embedding_with_failover("q", client_factory=factory)
    assert calls == ["proxy_primary", "proxy_rotated:10002", "proxy_rotated:7000"]


# ── _embed_query 维度守卫(降级路径诚实:错维度必须炸,绝不入查询) ────────


def test_embed_query_vector_size_guard(monkeypatch):
    monkeypatch.setattr(profile_recall, "check_budget", lambda *a, **k: True)
    monkeypatch.setattr(
        profile_recall,
        "_create_embedding_with_failover",
        lambda q, **k: (_FakeResp(8), "proxy_primary"),
    )
    with pytest.raises(RuntimeError, match="embedding_vector_size_mismatch:8"):
        profile_recall._embed_query("q")


def test_embed_query_reports_transport(monkeypatch):
    monkeypatch.setattr(profile_recall, "check_budget", lambda *a, **k: True)
    monkeypatch.setattr(profile_recall, "record_cost", lambda **k: None)
    monkeypatch.setattr(
        profile_recall,
        "_create_embedding_with_failover",
        lambda q, **k: (_FakeResp(profile_recall.VECTOR_SIZE), "proxy_rotated:7000"),
    )
    vector, meta = profile_recall._embed_query("q")
    assert len(vector) == profile_recall.VECTOR_SIZE
    assert meta["embedding_transport"] == "proxy_rotated:7000"
    assert meta["embedding_model"] == "text-embedding-3-small"


# ── 读侧 collection 维度守卫 ──────────────────────────────────────────────


class _FakeQdrant:
    def __init__(self, size):
        vectors = types.SimpleNamespace(size=size)
        params = types.SimpleNamespace(vectors=vectors)
        self._info = types.SimpleNamespace(config=types.SimpleNamespace(params=params))

    def get_collection(self, name):
        return self._info


def test_collection_dim_guard_mismatch_raises(monkeypatch):
    monkeypatch.setattr(profile_recall, "_collection_dim_verified", False)
    with pytest.raises(RuntimeError, match="qdrant_collection_dim_mismatch:3072"):
        profile_recall._assert_collection_dim(_FakeQdrant(3072))
    # 失败不缓存:下次仍会再查(修好 collection 后自动恢复)
    assert profile_recall._collection_dim_verified is False


def test_collection_dim_guard_match_caches(monkeypatch):
    monkeypatch.setattr(profile_recall, "_collection_dim_verified", False)
    profile_recall._assert_collection_dim(_FakeQdrant(1536))
    assert profile_recall._collection_dim_verified is True


def test_collection_dim_guard_unparseable_does_not_block(monkeypatch):
    monkeypatch.setattr(profile_recall, "_collection_dim_verified", False)

    class _Broken:
        def get_collection(self, name):
            raise RuntimeError("storage locked")

    profile_recall._assert_collection_dim(_Broken())  # 不抛:纵深防线,主防线在 embed 侧
    assert profile_recall._collection_dim_verified is False
