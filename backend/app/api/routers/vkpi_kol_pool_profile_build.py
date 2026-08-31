"""backend/app/api/routers/vkpi_kol_pool_profile_build.py

行为不变抽取:vkpi_kol_pool_intel.py 的「一键补全档案」写端点(档案 buildout 子域)。
本模块自带无 prefix 的 APIRouter,父 router 在原定义位置 include,路径/方法/name 逐字不变。

红线:零触 viltrox_fit_score;仅入队,幂等由下游各自去重。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

router = APIRouter()

logger = get_logger(__name__)


def _profile_build_unavailable(reason: str, kol_pool_id: int) -> HTTPException:
    """写服务不可用契约(与 vkpi_kol_pool_intel._write_service_error 同形,503→retryable)。"""
    return HTTPException(
        status_code=503,
        detail={
            "status": "unavailable",
            "reason": reason,
            "operation": "build_full_profile",
            "retryable": True,
            "kol_pool_id": int(kol_pool_id),
        },
    )


@router.post("/kol-pool/{kol_pool_id}/build-full-profile")
def build_full_profile_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
):
    """一键补全档案:强制 full 档点火(深爬 3 帖 + 评论采集;深析/受众/契合链自动跟进)。
    幂等(下游入队各自去重),约 3-5 分钟数据陆续点亮,抽屉既有轮询自动接住。零触 fit。"""
    from app.domains.discovery.buildout import build_full_profile
    from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError
    from app.domains.kol.video_tracking import VideoTrackingError

    try:
        result = build_full_profile(
            int(kol_pool_id),
            staff=staff if isinstance(staff, dict) else None,
        )
    except (MyKolPaidActionError, VideoTrackingError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("vkpi.build_full_profile_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        raise _profile_build_unavailable("profile_build_enqueue_failed", kol_pool_id) from exc
    if not isinstance(result, dict):
        raise _profile_build_unavailable("invalid_profile_build_result", kol_pool_id)
    out = dict(result)
    tier = str(out.get("tier") or "")
    if tier in {"full", "light"}:
        out.setdefault("status", "queued")
    elif str(out.get("reason") or "") == "error":
        out["status"] = "partial"
        out["reason"] = "profile_build_enqueue_partial"
    else:
        out.setdefault("status", "skipped")
    return out
