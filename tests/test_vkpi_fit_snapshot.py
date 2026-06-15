"""V6 Fit Top 快照管线测试 —— 重点守红线:capture 只读源 fit_score,绝不写回(指纹不变)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _fingerprint(conn):
    r = dict(
        conn.execute(
            "SELECT ROUND(SUM(viltrox_fit_score)::numeric,3) s, COUNT(*) c "
            "FROM vkpi_kol_pool WHERE viltrox_fit_score IS NOT NULL"
        ).fetchone()
    )
    return (str(r["s"]), int(r["c"]))


def test_capture_idempotent_and_fingerprint_unchanged():
    from app.db.connection import get_conn
    from app.domains.dashboard import fit_snapshot

    conn = get_conn()
    before = _fingerprint(conn)

    r1 = fit_snapshot.capture_daily_snapshot()
    assert r1["status"] == "ok"
    assert r1["rows"] > 0
    # 幂等:ON CONFLICT DO NOTHING → 同日重跑行数不变
    r2 = fit_snapshot.capture_daily_snapshot()
    assert r2["rows"] == r1["rows"]

    # 红线:快照只读源,绝不改 vkpi_kol_pool.viltrox_fit_score → 指纹不变
    assert _fingerprint(conn) == before


def test_compute_top_movers_shape_never_crashes():
    from app.domains.dashboard import fit_snapshot

    res = fit_snapshot.compute_top_movers(limit=5)
    assert isinstance(res, dict)
    assert "available" in res
    assert isinstance(res.get("movers"), list)
    # 不足两天 → warming_up;有两天 → 真 movers。两种都不崩、形状一致。
    if not res["available"]:
        assert res.get("reason") == "fit_history_warming_up"
