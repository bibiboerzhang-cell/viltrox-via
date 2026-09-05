"""Compatibility import for the shared, dependency-free Apify outcome type."""
from app.platform.apify_result_contract import ActorRunError, read_actor_dataset

__all__ = ["ActorRunError", "read_actor_dataset"]
