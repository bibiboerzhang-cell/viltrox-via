# Keep ``runtime`` available through Python's explicit package-submodule import
# fallback without loading it whenever an unrelated system service is imported.
from app.services.system import integrations, staff, trust_admin

__all__ = ["integrations", "runtime", "staff", "trust_admin"]
