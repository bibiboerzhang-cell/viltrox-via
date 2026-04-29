"""
V-OS Middleware — Party Layer (Phase 1)

Provides the unified customer master key (party_id) + identity stitching.
See migrations/010_party_layer.sql for schema.

Design principles:
    - party_id is UUIDv4 (no timestamp leakage)
    - email stored as SHA-256 hash only (raw + normalized both indexed)
    - Gmail normalization applied only to @gmail.com / @googlemail.com
    - No changes to existing users / via_sessions / orders tables
    - All PG-only (requires is_postgres_runtime())
"""
