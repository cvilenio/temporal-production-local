#!/bin/sh
# Create every Temporal namespace declared in the rendered OSS bootstrap spec.
#
# Shared by the Compose legacy path (temporal-create-namespace) and the kind
# bootstrap Job (via equivalent inline shell). Reads config/temporal/namespaces.yaml
# through render-oss-bootstrap.py output — no hardcoded domain name.
set -eu

BOOTSTRAP_ENV=${BOOTSTRAP_ENV:-/spec/.generated/oss-bootstrap.env}
TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS:-temporal:7233}
MAX_ATTEMPTS=${TEMPORAL_HEALTH_CHECK_MAX_ATTEMPTS:-30}
SLEEP_SECONDS=${TEMPORAL_HEALTH_CHECK_SLEEP_SECONDS:-5}

if [ ! -f "$BOOTSTRAP_ENV" ]; then
  echo "Missing $BOOTSTRAP_ENV — run via 'just render-oss-bootstrap' first." >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$BOOTSTRAP_ENV"

echo "Waiting for Temporal server port to be available..."
SERVER_HOST=$(echo "$TEMPORAL_ADDRESS" | cut -d: -f1)
SERVER_PORT=$(echo "$TEMPORAL_ADDRESS" | cut -d: -f2)
attempt=1
while ! nc -z -w 10 "$SERVER_HOST" "$SERVER_PORT"; do
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "Temporal server port did not become available after $MAX_ATTEMPTS attempts"
    exit 1
  fi
  echo "Temporal server port not ready yet, waiting... (attempt $attempt/$MAX_ATTEMPTS)"
  attempt=$((attempt + 1))
  sleep "$SLEEP_SECONDS"
done
echo "Temporal server port is available"

echo "Waiting for Temporal server to be healthy..."
attempt=1
while :; do
  if temporal operator cluster health --address "$TEMPORAL_ADDRESS"; then
    break
  fi
  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    echo "Server did not become healthy after $MAX_ATTEMPTS attempts"
    exit 1
  fi
  echo "Server not ready yet, waiting... (attempt $attempt/$MAX_ATTEMPTS)"
  attempt=$((attempt + 1))
  sleep "$SLEEP_SECONDS"
done

for NS in ${OSS_DOMAINS:-}; do
  ENV_KEY=$(echo "$NS" | tr '-' '_')
  eval "RET=\${OSS_RETENTION_${ENV_KEY}:-30}"

  echo "Ensuring namespace '$NS' (retention ${RET}d)..."
  attempt=1
  while :; do
    if temporal operator namespace describe -n "$NS" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
      echo "Namespace '$NS' already exists"
      break
    fi
    if temporal operator namespace create -n "$NS" --retention "${RET}d" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then
      echo "Namespace '$NS' created"
      break
    fi
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
      echo "Failed to create namespace '$NS' after $MAX_ATTEMPTS attempts"
      exit 1
    fi
    echo "Namespace operation not ready yet, waiting... (attempt $attempt/$MAX_ATTEMPTS)"
    attempt=$((attempt + 1))
    sleep "$SLEEP_SECONDS"
  done
done

echo "All OSS namespaces ready."
