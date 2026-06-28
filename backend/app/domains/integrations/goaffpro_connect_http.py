"""GOAFFPRO connect — 薄 HTTP client 簇(httpx 直连;无 creds -> not_configured,绝不抛)。

从 goaffpro_connect.py 行为不变搬出(move + re-export);本模块只含 REST 原语:
- _admin_headers:鉴权头拼装(X-GOAFFPRO-ACCESS-TOKEN / X-GOAFFPRO-PUBLIC-TOKEN)。
- _get / _post / _patch:单次请求骨架,creds-ready(无 token -> not_configured),绝不抛、
  绝不烧 LLM;HTTP 错误透出 body 给校准。

循环导入处理:本模块依赖 goaffpro_connect 的 get_credentials/_norm_base,这些在函数体内
lazy import(函数内 import),顶层绝不 import goaffpro_connect。
红线:本模块零 fit 写,绝不触碰 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

import httpx


def _admin_headers(creds: dict[str, Any]) -> dict[str, str]:
    """【待 key 校准】鉴权头按公开资料先设:
    X-GOAFFPRO-ACCESS-TOKEN(管理私钥,主)/ X-GOAFFPRO-PUBLIC-TOKEN(公钥,辅)。
    真 key 一到即对 Swagger 校准 header 名大小写与是否双发。
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = str(creds.get("access_token") or "")
    public = str(creds.get("public_token") or "")
    if token:
        headers["X-GOAFFPRO-ACCESS-TOKEN"] = token
    if public:
        headers["X-GOAFFPRO-PUBLIC-TOKEN"] = public
    return headers


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single GET against GOAFFPRO Admin API. creds-ready: no token -> not_configured.

    Returns {ok, data?, status_code?, reason?, error?}. Never burns an LLM; httpx direct.
    """
    from app.domains.integrations.goaffpro_connect import get_credentials, _norm_base  # lazy: 避免顶层循环导入

    creds = get_credentials()
    token = creds.get("access_token") or ""
    if not token:
        return {"ok": False, "reason": "not_configured"}
    base = _norm_base(creds.get("api_base"))
    url = f"{base}/{str(path or '').lstrip('/')}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.get(url, headers=_admin_headers(creds), params=params or {})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"http {exc.response.status_code}", "status_code": exc.response.status_code}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": data}


def _post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single POST against GOAFFPRO Admin API. creds-ready: no token -> not_configured.

    Returns {ok, data?, status_code?, reason?, error?}. Never burns an LLM, never raises;
    httpx direct. On HTTP error still tries to surface the JSON body (GOAFFPRO error msg)
    so callers can透出 raw 给校准。
    """
    from app.domains.integrations.goaffpro_connect import get_credentials, _norm_base  # lazy: 避免顶层循环导入

    creds = get_credentials()
    token = creds.get("access_token") or ""
    if not token:
        return {"ok": False, "reason": "not_configured"}
    base = _norm_base(creds.get("api_base"))
    url = f"{base}/{str(path or '').lstrip('/')}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(url, headers=_admin_headers(creds), json=body or {})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        body_text: Any = None
        try:
            body_text = exc.response.json()
        except Exception:  # noqa: BLE001 — body may be non-JSON; keep it as text
            try:
                body_text = exc.response.text
            except Exception:  # noqa: BLE001
                body_text = None
        return {
            "ok": False,
            "error": f"http {exc.response.status_code}",
            "status_code": exc.response.status_code,
            "raw": body_text,
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": data}


def _patch(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single PATCH against GOAFFPRO Admin API(改 affiliate:佣金/状态等)。
    Returns {ok, data?, status_code?, error?, raw?}。绝不抛;HTTP 错误透出 body。"""
    from app.domains.integrations.goaffpro_connect import get_credentials, _norm_base  # lazy: 避免顶层循环导入

    creds = get_credentials()
    token = creds.get("access_token") or ""
    if not token:
        return {"ok": False, "reason": "not_configured"}
    base = _norm_base(creds.get("api_base"))
    url = f"{base}/{str(path or '').lstrip('/')}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.patch(url, headers=_admin_headers(creds), json=body or {})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        body_text: Any = None
        try:
            body_text = exc.response.json()
        except Exception:  # noqa: BLE001
            try:
                body_text = exc.response.text
            except Exception:  # noqa: BLE001
                body_text = None
        return {"ok": False, "error": f"http {exc.response.status_code}", "status_code": exc.response.status_code, "raw": body_text}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "data": data}
