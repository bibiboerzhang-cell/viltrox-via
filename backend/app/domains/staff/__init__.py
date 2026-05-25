"""Staff domain facade."""

from app.domains.staff import kpi_ledger
from app.domains.staff.profile import (
    build_employee_workspace,
    build_staff_kpi,
    build_staff_profile,
    is_manager_staff,
    staff_directory,
)

__all__ = [
    "build_employee_workspace",
    "build_staff_kpi",
    "build_staff_profile",
    "is_manager_staff",
    "kpi_ledger",
    "staff_directory",
]
