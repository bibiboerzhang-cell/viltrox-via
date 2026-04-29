#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/runtime_env.sh"

RUNTIME_NGINX_DIR="${RUNTIME_NGINX_DIR:-$RUNTIME_ROOT/nginx}"
CERT_DIR="${CERT_DIR:-$RUNTIME_NGINX_DIR/certs}"
OPENSSL_CNF="${OPENSSL_CNF:-$CERT_DIR/openssl-local.cnf}"
CERT_KEY="${CERT_KEY:-$CERT_DIR/viltrox-local.key}"
CERT_CRT="${CERT_CRT:-$CERT_DIR/viltrox-local.crt}"

mkdir -p "$CERT_DIR"

cat > "$OPENSSL_CNF" <<'EOF'
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
C = US
ST = CA
L = Local
O = Viltrox
OU = V-OS
CN = localhost

[v3_req]
subjectAltName = @alt_names
basicConstraints = CA:TRUE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

openssl req \
  -x509 \
  -nodes \
  -days 365 \
  -newkey rsa:2048 \
  -keyout "$CERT_KEY" \
  -out "$CERT_CRT" \
  -config "$OPENSSL_CNF" \
  -extensions v3_req

echo "Generated local certificate:"
echo "  crt: $CERT_CRT"
echo "  key: $CERT_KEY"
