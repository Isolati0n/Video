#!/usr/bin/env bash
# Cloudflare WARP tunnel through a free anonymous wgcf account (MIT, github.com/ViRb3/wgcf).
# No Google/YouTube login is involved. Each "up" registers a fresh account, which usually
# gives a fresh egress IP.
#   scripts/warp.sh up     bring the tunnel up and verify connectivity (up to 3 tries)
#   scripts/warp.sh down
set -uo pipefail
WGCF_VERSION="${WGCF_VERSION:-2.3.1}"
ip() { curl -s --max-time 15 https://api.ipify.org || echo unknown; }

if [ "${1:-}" = down ]; then
  sudo wg-quick down wgcf 2>/dev/null || true
  echo "WARP down; egress ip: $(ip)"
  exit 0
fi

echo "egress ip before: $(ip)"
command -v wg-quick >/dev/null || sudo apt-get -o Acquire::Retries=3 install -y -qq --no-install-recommends wireguard-tools resolvconf >/dev/null
dir="${RUNNER_TEMP:-/tmp}/wgcf"
mkdir -p "$dir"
[ -x "$dir/wgcf" ] || {
  curl -sfL --retry 3 -o "$dir/wgcf" "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_amd64" || exit 1
  chmod +x "$dir/wgcf"
}
for i in 1 2 3; do
  sudo wg-quick down wgcf 2>/dev/null || true
  work=$(mktemp -d "$dir/acct.XXXXXX")
  if (cd "$work" && timeout 60 "$dir/wgcf" register --accept-tos >/dev/null 2>&1 && timeout 30 "$dir/wgcf" generate >/dev/null 2>&1); then
    sed -i 's/^DNS = .*/DNS = 1.1.1.1/' "$work/wgcf-profile.conf"
    sudo cp "$work/wgcf-profile.conf" /etc/wireguard/wgcf.conf
    if timeout 30 sudo wg-quick up wgcf >/dev/null 2>&1 \
       && timeout 25 curl -sf https://www.cloudflare.com/cdn-cgi/trace | grep -q '^warp=on'; then
      echo "WARP up (attempt $i); egress ip: $(ip)"
      exit 0
    fi
  fi
  echo "WARP attempt $i failed, retrying with a fresh account"
  sleep $((i * 5))
done
sudo wg-quick down wgcf 2>/dev/null || true
echo "::warning::WARP tunnel could not be established"
exit 1
