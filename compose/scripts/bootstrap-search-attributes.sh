#!/bin/sh
# Register custom search attributes for every domain in the OSS bootstrap spec.
#
# NON-PROD CONVENIENCE. The attribute set is NOT hardcoded here — it is rendered
# from the shared spec (config/temporal/namespaces.yaml) by
# render-oss-bootstrap.py into /spec/.generated/oss-bootstrap.env, so this stays
# in lockstep with the Cloud namespaces. The production-grade equivalent on kind
# is an Argo-managed Job rendered from the same spec (see ADR-0007).
set -eu

BOOTSTRAP_ENV=${BOOTSTRAP_ENV:-/spec/.generated/oss-bootstrap.env}
TEMPORAL_ADDRESS=${TEMPORAL_ADDRESS:-temporal:7233}

if [ ! -f "$BOOTSTRAP_ENV" ]; then
  echo "Missing $BOOTSTRAP_ENV — run via 'poe up' so the host renders it first." >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$BOOTSTRAP_ENV"

for NAMESPACE in ${OSS_DOMAINS:-}; do
  ENV_KEY=$(echo "$NAMESPACE" | tr '-' '_')
  eval "ATTRS=\${OSS_SEARCH_ATTRIBUTES_${ENV_KEY}:-}"

  echo "Waiting for namespace $NAMESPACE to be queryable..."
  until temporal operator search-attribute list --namespace "$NAMESPACE" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; do
    echo "  waiting on $NAMESPACE..."
    sleep 2
  done

  for pair in $ATTRS; do
    [ -n "$pair" ] || continue
    name=${pair%%=*}
    type=${pair#*=}
    echo "Registering search attribute $name ($type) on $NAMESPACE"
    temporal operator search-attribute create \
      --name "$name" --type "$type" --namespace "$NAMESPACE" \
      --address "$TEMPORAL_ADDRESS" || true
  done
done

echo "Search attribute bootstrap complete."
