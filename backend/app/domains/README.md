# Backend Domains

Domain modules own V-KPI business behavior. New feature code should land here instead of adding more files to `backend/app/services/vkpi`.

Each domain should expose a small facade that routers and scripts can call. Internal helpers stay private to the domain.

Initial domains:

- `dashboard`
- `data_quality`
- `intelligence`
- `market`
- `kol`
- `projects`
- `products`
- `attribution`
- `settings`
- `repair`
