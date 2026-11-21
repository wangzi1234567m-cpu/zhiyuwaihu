#!/bin/sh
# Install Docker Engine from Docker's official Ubuntu repository. Never remove conflicts automatically.
set -eu
if [ "$(id -u)" != 0 ]; then
    echo 'Run with: sudo sh scripts/install-docker-ubuntu24.sh' >&2
    exit 1
fi
. /etc/os-release
if [ "$ID" != ubuntu ] || [ "$VERSION_ID" != 24.04 ] || [ "$(dpkg --print-architecture)" != amd64 ]; then
    echo 'This installer is scoped to the inspected Ubuntu 24.04 amd64 server.' >&2
    exit 1
fi
if command -v docker >/dev/null 2>&1; then
    echo 'Docker already exists; inspect docker version and docker compose version first.'
    exit 0
fi
for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q '^install ok installed$'; then
        echo "Existing package $package needs review. No packages were removed." >&2
        exit 1
    fi
done
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl --connect-timeout 15 --max-time 90 -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version
docker compose version
