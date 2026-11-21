#!/bin/sh
# Run as root on the Linux server after the HTTP ACME location and HTTPS site are verified.
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
docker run --rm \
  -v "$project_dir/infra/private/letsencrypt:/etc/letsencrypt" \
  -v /var/lib/zhiyu-acme:/var/www/acme \
  certbot/certbot:latest renew --cert-name zhiyu-ip \
  --webroot --webroot-path /var/www/acme --quiet
nginx -t
systemctl reload nginx
