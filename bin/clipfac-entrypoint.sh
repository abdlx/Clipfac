#!/bin/sh
# Entrypoint: run the bgutil PO-token HTTP server next to the API, then exec
# the container command (uvicorn).
#
# yt-dlp gets PO tokens from bgutil. As a one-shot Node script
# (BGUTIL_SCRIPT_PATH) every call cold-started node and loaded jsdom +
# youtubei.js, which takes >15 s on this host and trips the getpot
# plugin's hardcoded 15 s version check, so downloads died before
# fetching anything. As a long-lived HTTP server the modules load once at
# boot and yt-dlp mints tokens over HTTP (BGUTIL_BASE_URL), so there is
# no per-call cold start. The server binds loopback only; only this
# container talks to it.
set -eu

BGUTIL_PORT="${BGUTIL_PORT:-4416}"
BGUTIL_HOST="${BGUTIL_HOST:-127.0.0.1}"
BGUTIL_URL="http://${BGUTIL_HOST}:${BGUTIL_PORT}"

# Restart loop: if the token server ever exits, bring it back. It runs in
# the background; the API is the foreground process (PID 1 via exec below),
# and the container exits with it.
(
  while true; do
    node /opt/bgutil-provider/server/build/main.js --host "$BGUTIL_HOST" --port "$BGUTIL_PORT" || true
    sleep 2
  done
) &
BGUTIL_LOOP_PID=$!

# Wait for the token server before starting the API, so the first
# download does not race a cold server. curl is in the image.
READY=0
i=0
while [ "$i" -lt 30 ]; do
  if curl -sf "$BGUTIL_URL/ping" > /dev/null 2>&1; then
    READY=1
    break
  fi
  i=$((i + 1))
  sleep 1
done

# If the server never came up, do not start the API into a state where
# every download fails: fail the container so the orchestrator retries it.
if [ "$READY" -ne 1 ]; then
  echo "bgutil token server did not become ready at $BGUTIL_URL" >&2
  kill "$BGUTIL_LOOP_PID" 2>/dev/null || true
  exit 1
fi

exec "$@"
