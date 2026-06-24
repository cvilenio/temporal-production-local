#!/bin/sh
# Register the orders custom search attributes on the local OSS namespace.
#
# NON-PROD CONVENIENCE. The attribute set is NOT hardcoded here — it is rendered
# from the shared spec (config/temporal/namespaces.yaml) by
# render-oss-bootstrap.py into /spec/.generated/oss-bootstrap.env, so this stays
# in lockstep with the Cloud namespaces. The production-grade equivalent on kind
# is an Argo-managed Job rendered from the same spec (see ADR-0007).
set -eu

BOOTSTRAP_ENV=${BOOTSTRAP_ENV:-/spec/.generated/oss-bootstrap.env}

if [ ! -f "$BOOTSTRAP_ENV" ]; then
  echo "Missing $BOOTSTRAP_ENV — run via 'poe up' so the host renders it first." >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$BOOTSTRAP_ENV"

NAMESPACE=${OSS_NAMESPACE:-ziggymart}

# Wait until the namespace is queryable before registering attributes.
until temporal operator search-attribute list --namespace "$NAMESPACE" >/dev/null 2>&1; do
  echo "Waiting for namespace $NAMESPACE..."
  sleep 2
done

# OSS_SEARCH_ATTRIBUTES is a space-separated list of NAME=TYPE pairs.
for pair in $OSS_SEARCH_ATTRIBUTES; do
  name=${pair%%=*}
  type=${pair#*=}
  echo "Registering search attribute $name ($type) on $NAMESPACE"
  temporal operator search-attribute create --name "$name" --type "$type" --namespace "$NAMESPACE" || true
done
