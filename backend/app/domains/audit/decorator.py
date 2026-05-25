"""backend/app/domains/audit/decorator.py

R59: 审计装饰器

把已有的 audit.log_business_event 包装成统一装饰器,
高敏感操作自动记录到 vkpi_business_audit_logs 表。

设计:
  - @audit_action(action_type="kol_import", target_type="kol_pool")
    endpoint 成功执行后,自动落审计
  - 装饰器从 staff 参数提取 staff_id
  - 装饰器从返回值或 kwargs 提取 target_id (可配置)
  - 失败 (异常) 也记录,但带 status=failed
  - 装饰器异常不影响业务返回 (audit 是 best-effort)

使用示例:
    @router.post("/kol_pool/import")
    @audit_action(action_type="kol_import", target_type="kol_pool")
    def import_kol(payload: dict, staff=Depends(require_tab("vkpi", "write"))):
        return kol_pool.import_items(...)
"""
from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from app.domains import audit


_logger = logging.getLogger(__name__)


def audit_action(
    *,
    action_type: str,
    target_type: str,
    target_id_extractor: Callable[[Any, dict], str] | None = None,
    detail_extractor: Callable[[Any, dict], str] | None = None,
    metadata_extractor: Callable[[Any, dict], dict] | None = None,
) -> Callable:
    """
    装饰器: 在 endpoint 成功执行后自动记录审计.
    
    参数:
      action_type:        动作类型 (例如 "kol_import" / "settings_update" / "feature_flag_toggle")
      target_type:        实体类型 (例如 "kol_pool" / "feature_flag" / "platform_settings")
      target_id_extractor: 从 (result, kwargs) 提取 target_id 的函数 (可选)
      detail_extractor:    从 (result, kwargs) 提取 detail 的函数 (可选)
      metadata_extractor:  从 (result, kwargs) 提取 metadata 的函数 (可选)
    
    默认行为:
      target_id 从 kwargs 取 (优先 'kol_id' / 'project_id' / 'target_id' / 'id')
      detail 留空
      metadata 包含 'action_status' (success | failed) 和 'kwargs_keys'
    
    异常处理:
      装饰器自身的异常不会被抛出,只记录 logger.warning
      被装饰函数的异常会原样抛出,但记录 status=failed 的审计
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            staff = kwargs.get("staff") or {}
            staff_id = _resolve_staff_id(staff)
            try:
                result = func(*args, **kwargs)
                if hasattr(result, "__await__"):
                    result = await result
                _safe_log_audit(
                    staff_id=staff_id,
                    action_type=action_type,
                    target_type=target_type,
                    target_id_extractor=target_id_extractor,
                    detail_extractor=detail_extractor,
                    metadata_extractor=metadata_extractor,
                    result=result,
                    kwargs=kwargs,
                    status="success",
                )
                return result
            except Exception as exc:
                _safe_log_audit(
                    staff_id=staff_id,
                    action_type=action_type,
                    target_type=target_type,
                    target_id_extractor=target_id_extractor,
                    detail_extractor=detail_extractor,
                    metadata_extractor=metadata_extractor,
                    result=None,
                    kwargs=kwargs,
                    status="failed",
                    error=str(exc),
                )
                raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            staff = kwargs.get("staff") or {}
            staff_id = _resolve_staff_id(staff)
            try:
                result = func(*args, **kwargs)
                _safe_log_audit(
                    staff_id=staff_id,
                    action_type=action_type,
                    target_type=target_type,
                    target_id_extractor=target_id_extractor,
                    detail_extractor=detail_extractor,
                    metadata_extractor=metadata_extractor,
                    result=result,
                    kwargs=kwargs,
                    status="success",
                )
                return result
            except Exception as exc:
                _safe_log_audit(
                    staff_id=staff_id,
                    action_type=action_type,
                    target_type=target_type,
                    target_id_extractor=target_id_extractor,
                    detail_extractor=detail_extractor,
                    metadata_extractor=metadata_extractor,
                    result=None,
                    kwargs=kwargs,
                    status="failed",
                    error=str(exc),
                )
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ─── 内部辅助 ─────────────────────────────────────────


def _resolve_staff_id(staff: Any) -> int:
    """从 staff 字典或对象提取 id"""
    if isinstance(staff, dict):
        return int(staff.get("id") or staff.get("staff_id") or 0)
    if hasattr(staff, "id"):
        return int(getattr(staff, "id", 0) or 0)
    return 0


def _safe_log_audit(
    *,
    staff_id: int,
    action_type: str,
    target_type: str,
    target_id_extractor: Callable | None,
    detail_extractor: Callable | None,
    metadata_extractor: Callable | None,
    result: Any,
    kwargs: dict[str, Any],
    status: str,
    error: str = "",
) -> None:
    """装饰器内部:记录审计但永不抛异常"""
    if not staff_id:
        # 无 staff_id 不记录 (audit.log_business_event 也会拒绝)
        return
    
    try:
        # 提取 target_id
        if target_id_extractor:
            target_id = target_id_extractor(result, kwargs)
        else:
            target_id = _default_target_id(kwargs, result)
        
        # 提取 detail
        if detail_extractor:
            detail = detail_extractor(result, kwargs)
        else:
            detail = ""
        
        # 提取 metadata
        if metadata_extractor:
            metadata = metadata_extractor(result, kwargs)
        else:
            metadata = {}
        
        # 加上 status / error
        metadata = dict(metadata or {})
        metadata.setdefault("action_status", status)
        if error:
            metadata["error"] = error
        if "kwargs_keys" not in metadata:
            metadata["kwargs_keys"] = sorted(
                k for k in kwargs.keys()
                if k not in {"staff", "request", "background_tasks"}
            )
        
        audit.log_business_event(
            staff_id=staff_id,
            action_type=action_type,
            target_type=target_type,
            target_id=str(target_id or ""),
            detail=str(detail or ""),
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        # audit 失败不影响业务
        _logger.warning("audit_decorator failed silently: %s", exc)


def _default_target_id(kwargs: dict[str, Any], result: Any) -> str:
    """
    默认 target_id 提取逻辑:
      1. kwargs 里找 kol_id / project_id / kol_pool_id / target_id / id
      2. result 是 dict 时找 id / pool_uid / kol_id
      3. 都没有返回空字符串
    """
    candidates = ["kol_id", "project_id", "kol_pool_id", "target_id", "id", "flag_key", "platform"]
    for key in candidates:
        if key in kwargs and kwargs[key] not in (None, ""):
            return str(kwargs[key])
    
    if isinstance(result, dict):
        for key in candidates:
            if key in result and result[key] not in (None, ""):
                return str(result[key])
        # 嵌套 result.item.id
        for nested_key in ["item", "kol", "kol_pool", "data"]:
            nested = result.get(nested_key)
            if isinstance(nested, dict):
                for key in candidates:
                    if key in nested and nested[key] not in (None, ""):
                        return str(nested[key])
    
    return ""
