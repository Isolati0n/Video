#!/usr/bin/env bash
# usage: scripts/download_via_warp.sh <video_id> <workdir>
# Downloads through a fresh WARP tunnel; if YouTube still blocks (exit 75), tears the tunnel
# down and tries again with a new account/IP, up to ATTEMPTS times. Other failures
# (private, removed, ...) are not retried. USE_WARP=false skips the tunnel.
set -uo pipefail
vid="$1"; work="$2"
attempts="${ATTEMPTS:-5}"
for n in $(seq 1 "$attempts"); do
  echo "::group::download attempt $n/$attempts"
  if [ "${USE_WARP:-true}" != false ]; then
    scripts/warp.sh up || { echo "no tunnel on attempt $n"; echo "::endgroup::"; sleep 10; continue; }
  fi
  python -m ytdigest.prepare download "$vid" "$work"; rc=$?
  [ "${USE_WARP:-true}" != false ] && scripts/warp.sh down
  echo "::endgroup::"
  if [ $rc -eq 0 ]; then
    echo "::notice title=WARP download::succeeded on attempt $n of $attempts"
    exit 0
  fi
  [ $rc -ne 75 ] && exit $rc
  # Observed: refusals come in streaks (5 refused within a minute, then a success a few minutes later),
  # so the pause grows quickly: 5, 20, 45, 80 s
  echo "YouTube refused attempt $n; retrying with a fresh tunnel after $((n * n * 5)) s"
  sleep $((n * n * 5))
done
echo "::error::YouTube blocked every attempt"
exit 75
