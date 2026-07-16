"""Marketing Advisor: private conversations, draft-only actions and owner-confirmed memory."""

from app.domains.advisor.scope import AdvisorScope, AdvisorScopeError, advisor_scope_from_staff

__all__ = ["AdvisorScope", "AdvisorScopeError", "advisor_scope_from_staff"]
