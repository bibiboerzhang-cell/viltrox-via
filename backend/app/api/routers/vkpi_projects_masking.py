"""V-KPI projects router helpers: scope 403 mapping + payment-field masking.

行为不变搬迁(从 vkpi_projects.py 整体 move):收款敏感字段遮蔽簇 +
_scope_403。原文件 re-export 兜住所有调用点,函数体逐字不变。
红线:此模块零 fit 写,纯遮蔽/读路径。
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from app.domains.access import scope

MAX_CONTRACT_UPLOAD_BYTES = 25 * 1024 * 1024


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


# 波2 R2 收口(2026-06-12):原 key-regex 只盖 payment/account/bank 类结构化键,
# 漏了三条旁路——顶层 fee_amount/fee_currency、manual_overrides_json 内费用键、
# raw_extracted_json.summary 散文(会回声「总费用/分期支付」付款节奏)。
# 现把 fee 类键(fee_amount/fee_currency/total_fee…)与 summary/payment_terms 类
# 散文键一并纳入遮蔽;_safe_row 已把 *_json 列 loads 成 dict,递归可达嵌套键。
# 豁免集不变:finance/cost can_view_all 或项目成员(见 _mask_payment_fields)。
_PAYMENT_KEY_RE = re.compile(
    r"payment|account|iban|swift|bank|payee|beneficiary|routing|fee|summary",
    re.IGNORECASE,
)
_PAYMENT_MASK = "***"


def _mask_payment_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (_PAYMENT_MASK if _PAYMENT_KEY_RE.search(str(key)) and val not in (None, "", [], {}) else _mask_payment_values(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_mask_payment_values(item) for item in value]
    return value


def _mask_payment_fields(result: Any, staff: dict | None, *, project_id: int | None = None) -> Any:
    """批D 收款遮蔽(2026-06-12)+ 波2 R2 收口:合同详情/列表返回里的收款敏感字段
    (payment/account/iban/swift/bank 类 + fee_amount/fee_currency 费用键 +
    summary/payment_terms 散文键,含 raw_extracted_json/manual_overrides_json 等
    json 内嵌套键)对非 can_view_all(finance/cost 域)且非项目 assigned/creator
    的员工遮蔽为 "***"。空值保留原样,前端可区分"未填"与"被遮蔽"。
    注:_mask_payment_values 逐层重建 dict/list,原对象不被就地篡改。"""
    if scope.can_view_all(staff, domain="finance") or scope.can_view_all(staff, domain="cost"):
        return result
    if project_id is not None and scope.is_project_member(int(project_id), staff):
        return result
    return _mask_payment_values(result)
