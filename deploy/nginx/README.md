Viltrox 2.0 Nginx Front Door

This folder contains the reverse-proxy template for `viltrox-2.0`.

Recommended hostnames:

- `lab.viltrox.com` -> public web (`127.0.0.1:8101`)
- `admin.viltrox.com` -> admin web (`127.0.0.1:8102`)

What this config handles:

- HTTP -> HTTPS redirect
- TLS termination
- `client_max_body_size 550m` for large upload flows
- SSE proxy tuning for `/api/audit/stream/*`
- static asset caching for Vite build assets
- separation between public and admin upstreams

Install steps:

1. Copy `viltrox-2.0.conf` into `/etc/nginx/sites-available/`.
2. Replace the placeholder domains and certificate paths.
3. Symlink it into `/etc/nginx/sites-enabled/`.
4. Run `nginx -t`.
5. Reload Nginx.

If you use Certbot:

- obtain certs for both hostnames first
- then point `ssl_certificate` / `ssl_certificate_key` at the generated files
- verify renewal with `certbot renew --dry-run`

Example bootstrap:

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo cp deploy/nginx/viltrox-2.0.conf /etc/nginx/sites-available/viltrox-2.0.conf
sudo ln -sf /etc/nginx/sites-available/viltrox-2.0.conf /etc/nginx/sites-enabled/viltrox-2.0.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d lab.viltrox.com -d admin.viltrox.com
```

Post-enable validation:

```bash
curl -I http://lab.viltrox.com
curl -I https://lab.viltrox.com/health
curl -I https://admin.viltrox.com/health
```

## Local HTTPS

You can also run the same reverse-proxy model locally without touching system ports.

Local endpoints:

- `https://localhost:8443` -> public web (`127.0.0.1:8101`)
- `https://localhost:9443` -> admin web (`127.0.0.1:8102`)

Repo assets:

- `deploy/nginx/viltrox-2.0.local.conf`
- `scripts/generate_local_ssl.sh`
- `scripts/start_local_https_proxy.sh`
- `scripts/stop_local_https_proxy.sh`

The local flow uses a self-signed certificate generated under `runtime/nginx/certs/`.
The helper prefers the vendored binary at `runtime/vendor/nginx/sbin/nginx` and only falls back to a system `nginx` install when needed.

Example:

```bash
cd /Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0
source ./scripts/runtime_env.sh
APP_ROLE=public-web ./scripts/start_public.sh
APP_ROLE=admin-web ./scripts/start_admin.sh
./scripts/start_local_https_proxy.sh
```

If no vendored or system `nginx` binary is available yet, the start script stops with a clear message instead of failing silently.
