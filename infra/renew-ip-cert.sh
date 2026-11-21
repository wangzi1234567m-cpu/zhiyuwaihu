#!/bin/sh
# Run on the Linux server every 12 hours after the IP HTTPS deployment is healthy.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
# Skip overlapping cron/manual runs without interrupting the current renewal.
exec 9> "$project_dir/infra/private/renew-ip-cert.lock"
flock -n 9 || exit 0
docker run --rm \
  -v "$project_dir/infra/private/letsencrypt:/etc/letsencrypt" \
  -v "$project_dir/infra/private/acme-webroot:/var/www/acme" \
  certbot/certbot:latest renew --cert-name zhiyu-ip \
  --webroot --webroot-path /var/www/acme --non-interactive --quiet
# Reload even when filenames are unchanged, so the manually loaded certificate is reread.
docker compose -f compose.yaml -f compose.cloud.yaml -f compose.public-ip.yaml \
  exec -T gateway caddy reload --config /etc/caddy/Caddyfile --force
