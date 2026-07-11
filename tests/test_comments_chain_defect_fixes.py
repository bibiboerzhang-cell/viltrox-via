"""评论链后端缺陷修复回归(2026-07-11 四路核查坐实后的逐刀修)。

覆盖面(全部零真库、零外调、零 LLM):
  ① worker 未知 job_type 防线:不在已知集合 → _block_job('unknown_job_type'),
     绝不滑进 derive_method 缺省='mock' 的假成功路径;
  ① 官号评论采集 handler 注册:official_channel_comments_collect 显式分派到
     _process_official_channel_comments_collect;
  ② blocked 终态自动重试:all_posts_failed 且逐帖 error 属可重试类(网络/5xx/522/
     timeout 词族)→ 抛异常走统一 failure→requeue 通道;不可重试类保持 blocked;
  ④ 官号帖快照断链回退:最新快照 raw_payload_json 空('{}')→ 回退最近一条
     LENGTH>2 的快照(SQL 层 + Python 层双兜);
  ⑤ facebook 评论时间戳:created_at 键列表含 'date'(Apify actor 真实键);
  ⑥ owned 评论回链:_save_channel_comments 写 post_id=channel_id(不再写死 0),
     voice_feed owned JOIN(ec.id = c.post_id)语义对齐;
  ⑦⑧ /by-post 读口宪法:author_handle 脱敏(首字符+***)、author_id/raw_data_json
     不入 SELECT、排序 COALESCE(created_at, fetched_at) DESC、增量补
     language_detected + sentiment 关联字段。

红线:零触 viltrox_fit_score / rule_v0;纯源码反射 + mock conn。
"""
from __future__ import annotations

import inspect
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 假 psycopg 连接(handler 终态 UPDATE 用)────────────────────────────────


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self._sink.append((" ".join(str(sql).split()), tuple(params)))


class _FakePGConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return nullcontext()

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self.calls)


# ── 假 compat 连接(channel._save_channel_comments 用)──────────────────────


class _FakeCompatResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


class _FakeCompatConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).split()), tuple(params)))
        if "COUNT(" in str(sql):
            return _FakeCompatResult({"n": 0})
        return _FakeCompatResult(None)

    def commit(self):
        pass


# ── ① 未知 job_type 防线 ────────────────────────────────────────────────────


def test_unknown_job_type_blocked_not_mock(monkeypatch):
    """不认识的 job_type 必须 _block_job('unknown_job_type'),绝不落 mock 假成功。"""
    from app.workers import apify_jobs_worker as worker

    blocked: list[tuple[int, str, dict]] = []
    monkeypatch.setattr(
        worker,
        "_block_job",
        lambda conn, job_id, reason, detail=None: blocked.append((job_id, reason, detail or {})),
    )
    # payload 带齐 target_type/target_id + 缺省 derive_method → 旧代码会滑进 mock 路径。
    job = {"id": 999, "job_type": "totally_bogus_job_type", "payload": {"target_type": "official_channel", "target_id": 1}}
    worker._process_job(None, job)
    assert blocked == [(999, "unknown_job_type", {"job_type": "totally_bogus_job_type"})]


def test_target_fallback_whitelist_is_video_only():
    from app.workers import apify_jobs_worker as worker

    assert worker.TARGET_FALLBACK_JOB_TYPES == frozenset({"video"})
    src = inspect.getsource(worker._process_job)
    # 防线必须在 _target 兜底之前
    assert src.index("TARGET_FALLBACK_JOB_TYPES") < src.index("_target(payload)")
    assert "unknown_job_type" in src


# ── ① 官号评论采集 handler 注册 ─────────────────────────────────────────────


def test_official_channel_comments_collect_routed(monkeypatch):
    from app.workers import apify_jobs_worker as worker

    src = inspect.getsource(worker._process_job)
    assert '"official_channel_comments_collect"' in src
    called: list[dict] = []
    monkeypatch.setattr(
        worker,
        "_process_official_channel_comments_collect",
        lambda conn, job, payload: called.append(payload),
    )
    worker._process_job(None, {"id": 1, "job_type": "official_channel_comments_collect", "payload": {"channel_id": 102}})
    assert called == [{"channel_id": 102}]


def test_official_handler_marks_done_on_ready(monkeypatch):
    from app.workers import apify_jobs_worker_handlers as handlers

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    from app.domains.comments import channel as comments_channel

    monkeypatch.setattr(
        comments_channel,
        "run_official_channel_comments_for_job",
        lambda payload, staff=None: {"status": "ready", "channel_id": 102, "posts": 3, "ok": 3, "skipped": 0, "new_comments": 7, "results": []},
    )
    conn = _FakePGConn()
    payload = {"channel_id": 102, "staff_id": 84}
    handlers._process_official_channel_comments_collect(conn, {"id": 5}, payload)
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert params[0] == "done"
    assert payload["comments_collect_result"]["new_comments"] == 7


# ── ② all_posts_failed 可重试类 → 抛异常走统一 requeue 通道 ────────────────


def test_retryable_error_classifier():
    from app.workers.apify_jobs_worker_handlers import _comments_failed_errors_retryable

    retryable, sample = _comments_failed_errors_retryable(
        [{"status": "fail", "error": "522 Server Error"}, {"status": "fail", "error": "connection reset by peer"}]
    )
    assert retryable and "522" in sample
    # 不可重试类(URL 无效/缺 external id)→ 保持 blocked
    retryable, _ = _comments_failed_errors_retryable([{"status": "fail", "error": "post missing external_post_id"}])
    assert not retryable
    # 无 error 文本 → 不可判 → 保守 False
    retryable, _ = _comments_failed_errors_retryable([{"status": "fail", "error": ""}])
    assert not retryable
    # 混合(一条可重试一条永久)→ 保守 False
    retryable, _ = _comments_failed_errors_retryable(
        [{"status": "fail", "error": "522 Server Error"}, {"status": "fail", "error": "post not found in vkpi_kol_video_evidence"}]
    )
    assert not retryable


def test_kol_pool_all_failed_retryable_raises(monkeypatch):
    from app.workers import apify_jobs_worker_handlers as handlers
    from app.domains.comments import collector as comments_collector

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(
        comments_collector,
        "run_kol_pool_comments_for_job",
        lambda payload, staff=None: {
            "status": "blocked:all_posts_failed",
            "kol_pool_id": 3450,
            "posts": 8,
            "ok": 0,
            "new_comments": 0,
            "results": [{"evidence_id": 1756, "status": "fail", "new": 0, "error": "522 Server Error"}],
        },
    )
    conn = _FakePGConn()
    with pytest.raises(RuntimeError, match="comments_collect_all_posts_failed_retryable"):
        handlers._process_kol_pool_comments_collect(conn, {"id": 3313}, {"kol_pool_id": 3450, "staff_id": 84})
    assert conn.calls == []  # 没写终态 —— 交给 _fail_job 统一分流


def test_kol_pool_all_failed_permanent_stays_blocked(monkeypatch):
    from app.workers import apify_jobs_worker_handlers as handlers
    from app.domains.comments import collector as comments_collector

    monkeypatch.setattr(handlers, "db_connection_sync_scope", lambda: nullcontext())
    monkeypatch.setattr(
        comments_collector,
        "run_kol_pool_comments_for_job",
        lambda payload, staff=None: {
            "status": "blocked:all_posts_failed",
            "kol_pool_id": 1,
            "posts": 1,
            "ok": 0,
            "new_comments": 0,
            "results": [{"evidence_id": 9, "status": "fail", "new": 0, "error": "post missing external_post_id"}],
        },
    )
    conn = _FakePGConn()
    handlers._process_kol_pool_comments_collect(conn, {"id": 7}, {"kol_pool_id": 1, "staff_id": 84})
    assert len(conn.calls) == 1
    _, params = conn.calls[0]
    assert params[0] == "blocked"


def test_official_runner_honest_all_failed_status():
    """官号 runner 不再全败也报 ready;逐帖 error 随行带出供 worker 判可重试。"""
    from app.domains.comments import channel as comments_channel

    src = inspect.getsource(comments_channel.run_official_channel_comments_for_job)
    assert "all_posts_failed" in src
    assert '"error"' in src


# ── ④ 快照断链回退 ─────────────────────────────────────────────────────────


def test_latest_channel_row_sql_has_nonempty_fallback():
    from app.domains.channels import posts as channel_posts

    src = inspect.getsource(channel_posts._latest_channel_row)
    assert "LENGTH(COALESCE(m.raw_payload_json, '')) > 2" in src
    assert "LENGTH(COALESCE(mn.raw_payload_json, '')) > 2" in src
    assert "mr.raw_payload_json" in src


def test_all_posts_for_channel_python_fallback(monkeypatch):
    from app.domains.channels import evidence as channels_evidence

    fake = _FakeCompatConn()

    class _RawResult:
        def fetchone(self):
            return {"raw_payload_json": '{"package_dir": "tmp/vkpi_channel_packages/x", "posts": {"items": []}}'}

    monkeypatch.setattr(channels_evidence, "get_conn", lambda: fake)
    fake.execute = lambda sql, params=(): _RawResult()  # type: ignore[assignment]
    raw = channels_evidence._fallback_nonempty_snapshot_raw({"id": 102})
    assert raw.get("package_dir") == "tmp/vkpi_channel_packages/x"
    # 空行/无 id → 空 dict,不炸
    assert channels_evidence._fallback_nonempty_snapshot_raw({}) == {}
    # _all_posts_for_channel 在 raw 为空时必须走回退
    src = inspect.getsource(channels_evidence._all_posts_for_channel)
    assert "_fallback_nonempty_snapshot_raw" in src


# ── ⑤ facebook created_at 'date' 键映射 ────────────────────────────────────


def test_facebook_comment_date_key_mapped():
    from app.domains.comments.collector import _standardize_comment

    std = _standardize_comment(
        {"id": "c-fb-1", "text": "nice lens", "date": "2026-06-20T08:30:00.000Z", "profileName": "Frank Z"},
        platform="facebook",
        post_id=102,
        account_id=102,
        external_post_id="fbpost1",
        post_table="vkpi_employee_channels",
    )
    assert std["created_at"] == "2026-06-20T08:30:00.000Z"
    # 显式键仍优先于 date
    std2 = _standardize_comment(
        {"id": "c-fb-2", "text": "x", "createdTime": "2026-06-01T00:00:00Z", "date": "2026-06-20T08:30:00.000Z"},
        platform="facebook",
        post_id=102,
        account_id=102,
        external_post_id="fbpost1",
        post_table="vkpi_employee_channels",
    )
    assert std2["created_at"] == "2026-06-01T00:00:00Z"


# ── ⑥ owned 评论回链(写端 post_id=channel_id;读端 JOIN 语义对齐)────────────


def test_save_channel_comments_writes_channel_id_as_post_id(monkeypatch):
    from app.domains.comments import channel as comments_channel
    from app.domains.comments import collector as comments_collector

    fake = _FakeCompatConn()
    monkeypatch.setattr(comments_channel, "get_conn", lambda: fake)
    monkeypatch.setattr(comments_collector, "ensure_vkpi_comments_schema", lambda: None)
    new_count = comments_channel._save_channel_comments(
        channel_id=104,
        platform="youtube",
        external_post_id="yt-post-1",
        comments=[{"id": "yt-c-1", "text": "great video"}],
    )
    inserts = [(sql, params) for sql, params in fake.calls if sql.startswith("INSERT INTO vkpi_comments")]
    assert len(inserts) == 1
    _, params = inserts[0]
    assert params[0] == 104  # account_id
    assert params[1] == 104  # post_id = channel_id(此前写死 0 → voice_feed owned JOIN 永不命中)
    assert params[2] == "vkpi_employee_channels"
    assert new_count == 0  # 假 conn 前后 COUNT 都是 0,函数不炸即可


def test_voice_feed_owned_join_semantics_match_write_side():
    from app.domains.market import voice_feed

    # 读端 owned JOIN 按 ec.id = c.post_id;写端(⑥)已改为 post_id=channel_id,语义对齐。
    assert "ON c.post_table = 'vkpi_employee_channels' AND ec.id = c.post_id" in voice_feed.FEED_SELECT_SQL
    # 宪法注释:全评论读口统一
    assert "全评论读口统一" in (voice_feed.__doc__ or "")


# ── ⑦⑧ /by-post 读口:脱敏 + 排序 + 增量字段 ───────────────────────────────


def test_by_post_masks_author_handle_and_never_selects_private_columns():
    from app.api.routers import vkpi_comments as router_mod

    assert router_mod._mask_author_handle("frank_of_all_trades") == "f***"
    assert router_mod._mask_author_handle("") is None
    assert router_mod._mask_author_handle(None) is None
    src = inspect.getsource(router_mod.api_comments_by_post)
    assert "_mask_author_handle" in src
    # 只审 SQL 段(函数 docstring 里会提到禁入列名,不算泄漏)
    sql = src[src.index("SELECT c.id") : src.index("LIMIT ?")]
    assert "author_id" not in sql
    assert "raw_data_json" not in sql
    assert "ORDER BY COALESCE(c.created_at, c.fetched_at) DESC" in sql
    assert "language_detected" in sql
    assert "s.sentiment" in sql and "LEFT JOIN vkpi_sentiment_results" in sql


# ── ③ Apify 直连(runtime_env.sh NO_PROXY)──────────────────────────────────


def test_runtime_env_no_proxy_includes_apify():
    text = (REPO_ROOT / "scripts" / "runtime_env.sh").read_text(encoding="utf-8")
    assert 'NO_PROXY="$NO_PROXY,api.apify.com"' in text
    # LLM 三家照旧走代理:绝不许把它们塞进 NO_PROXY
    for host in ("api.openai.com", "generativelanguage", "api.anthropic.com"):
        assert f"NO_PROXY,{host}" not in text and f"{host}," not in text.split("api.apify.com")[0].split("NO_PROXY=")[-1]
    assert "HTTPS_PROXY" in text
