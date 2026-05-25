# Backend Platform

Platform modules own cross-cutting infrastructure: DB access, auth, RBAC, audit, jobs, providers, budget, media, and observability.

Business domains may depend on platform modules. Platform modules must not depend on business domains.
