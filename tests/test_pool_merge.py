"""Tests for KOL Pool 去重归并器 (L6).

覆盖:
  - detect_duplicate_master 只读检测(email 强信号 / 模糊信号区分);
  - dedupe_enrolled_pool_row enroll 落库后 hook(email 自动合并、模糊只进人工清单);
  - reconcile_pool_duplicates 全池扫描(默认 dry_run 只读、对称对去重);
  - 红线:归并前后 viltrox_fit_score 守恒(本测试全程绝不写该列)。

不触红线:种子行用裸 INSERT 只写 profile-basics 列 + email,**绝不** INSERT/UPDATE
viltrox_fit_score(留 NULL),归并路径靠 apply_merge 自带 before/after 守卫保证不变。
"""
from __future__ import annotations

import secrets

import pytest

from app.db.connection import get_conn
from app.domains.kol import pool_merge


MARKER = "vkpi-pool-merge-unit"


def _seed_row(conn, *, platform: str, handle: str, email: str = "", bio: str = "", profile_url: str = "") -> int:
    uid = f"{MARKER}-{secrets.token_hex(6)}"
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, email, bio, profile_url, source_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (uid, platform, handle, email, bio, profile_url, MARKER),
    )
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE platform=? AND handle=? ORDER BY id DESC LIMIT 1",
        (platform, handle),
    ).fetchone()
    return int(dict(row)["id"])


@pytest.fixture
def seeded_pair():
    conn = get_conn()
    suffix = secrets.token_hex(4)

    def cleanup() -> None:
        ids = [
            int(dict(r)["id"])
            for r in conn.execute(
                "SELECT id FROM vkpi_kol_pool WHERE source_ref=? OR pool_uid LIKE ?",
                (MARKER, f"{MARKER}-%"),
            ).fetchall()
        ]
        for pid in ids:
            conn.execute("DELETE FROM vkpi_kol_pool_aliases WHERE kol_pool_id=?", (pid,))
        # 先清指针再删行,避免 FK 自引用残留
        for pid in ids:
            conn.execute("UPDATE vkpi_kol_pool SET duplicate_of_id=NULL WHERE id=?", (pid,))
        for pid in ids:
            conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (pid,))
        conn.commit()

    cleanup()
    # 两条跨平台同一人:email 完全一致 = 强信号(auto-eligible)。
    email = f"{MARKER}-{suffix}@example.com"
    a = _seed_row(conn, platform="youtube", handle=f"creator-{suffix}", email=email)
    b = _seed_row(conn, platform="instagram", handle=f"creator_{suffix}_ig", email=email)
    conn.commit()
    yield {"low": min(a, b), "high": max(a, b), "email": email}
    cleanup()


def test_detect_email_is_auto_eligible(seeded_pair):
    conn = get_conn()
    det = pool_merge.detect_duplicate_master(seeded_pair["high"], conn=conn)
    assert det["candidate_master_id"] == seeded_pair["low"] or det["candidate_master_id"] == seeded_pair["high"]
    assert det["signal"] == "email"
    assert det["auto_eligible"] is True


def test_canonical_pair_collapses_symmetric():
    low, high = 100, 200
    assert pool_merge._canonical_master_pair(high, low) == (high, low)
    assert pool_merge._canonical_master_pair(low, high) == (high, low)


def test_dedupe_hook_auto_merges_email_and_preserves_fit(seeded_pair):
    conn = get_conn()
    low, high = seeded_pair["low"], seeded_pair["high"]
    # fit 守卫基线:两行 fit 在合并前的快照(本测试从不写该列 → 应为 NULL)。
    before = {
        int(dict(r)["id"]): dict(r)
        for r in conn.execute(
            "SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id IN (?, ?)",
            (low, high),
        ).fetchall()
    }
    res = pool_merge.dedupe_enrolled_pool_row(high, auto_merge=True, conn=conn)
    assert res["ok"] is True
    assert res["merged"] is True
    assert res["signal"] == "email"
    # 指针:高 id 行 duplicate_of_id 指向低 id 主行。
    dup_row = dict(conn.execute("SELECT duplicate_of_id FROM vkpi_kol_pool WHERE id=?", (high,)).fetchone())
    assert int(dup_row["duplicate_of_id"]) == low
    # 红线:两行 fit 字段归并前后守恒。
    after = {
        int(dict(r)["id"]): dict(r)
        for r in conn.execute(
            "SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id IN (?, ?)",
            (low, high),
        ).fetchall()
    }
    for pid in (low, high):
        assert before[pid]["viltrox_fit_score"] == after[pid]["viltrox_fit_score"]
        assert before[pid]["viltrox_fit_reason"] == after[pid]["viltrox_fit_reason"]


def test_dedupe_hook_fuzzy_signal_does_not_auto_write():
    """模糊信号(profile_link)不自动写:落人工清单,duplicate_of_id 保持 NULL。"""
    conn = get_conn()
    suffix = secrets.token_hex(4)

    def cleanup() -> None:
        ids = [int(dict(r)["id"]) for r in conn.execute(
            "SELECT id FROM vkpi_kol_pool WHERE source_ref=?", (MARKER,)).fetchall()]
        for pid in ids:
            conn.execute("DELETE FROM vkpi_kol_pool_aliases WHERE kol_pool_id=?", (pid,))
            conn.execute("UPDATE vkpi_kol_pool SET duplicate_of_id=NULL WHERE id=?", (pid,))
        for pid in ids:
            conn.execute("DELETE FROM vkpi_kol_pool WHERE id=?", (pid,))
        conn.commit()

    cleanup()
    try:
        # detect(dup) 拿 dup 的 handle 去候选 master 的 bio/profile_url 里找 → master 的 bio 必须含 dup handle。
        dup_handle = f"other{suffix}"
        master = _seed_row(conn, platform="youtube", handle=f"hub{suffix}", bio=f"cross-posted from {dup_handle} on ig")
        # 模糊 profile_link 信号(非 email,非 handle+name):dup 与 master handle/name 都不同。
        dup = _seed_row(conn, platform="instagram", handle=dup_handle)
        conn.commit()
        res = pool_merge.dedupe_enrolled_pool_row(dup, auto_merge=True, conn=conn)
        assert res["merged"] is False
        assert res.get("needs_review") is True
        # 绝不自动写指针。
        for pid in (dup, master):
            ptr = dict(conn.execute("SELECT duplicate_of_id FROM vkpi_kol_pool WHERE id=?", (pid,)).fetchone())
            assert ptr["duplicate_of_id"] is None
    finally:
        cleanup()


def test_reconcile_dry_run_is_read_only(seeded_pair):
    conn = get_conn()
    res = pool_merge.reconcile_pool_duplicates(dry_run=True, conn=conn)
    assert res["dry_run"] is True
    assert res["merged_count"] == 0
    # 我们的种子对应出现在 auto 清单里(email 强信号),且对称对只计一次。
    pair_keys = {(p["master_id"], p["duplicate_id"]) for p in res["auto_pairs"]}
    assert (seeded_pair["low"], seeded_pair["high"]) in pair_keys
    # dry_run 绝不写指针。
    dup_row = dict(conn.execute(
        "SELECT duplicate_of_id FROM vkpi_kol_pool WHERE id=?", (seeded_pair["high"],)).fetchone())
    assert dup_row["duplicate_of_id"] is None
