"""Async task domain package.

Import concrete task adapters explicitly (for example,
``app.domains.tasks.enqueue``).  Keeping package initialization inert prevents
an import of the domain namespace from binding database-backed implementations.
"""
