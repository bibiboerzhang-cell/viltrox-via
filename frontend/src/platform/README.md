# Frontend Platform

Platform modules own API transport, auth/session state, role gates, telemetry, runtime config, and other cross-cutting browser infrastructure.

Business domains may depend on platform modules. Platform modules must not depend on business domains.
