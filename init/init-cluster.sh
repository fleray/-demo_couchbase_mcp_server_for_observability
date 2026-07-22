#!/bin/bash
# Idempotent Couchbase demo cluster bootstrap.
# Safe to re-run: skips steps that are already done.
set -uo pipefail

CB_HOST="${CB_HOST:-couchbase}"
CB_USERNAME="${CB_USERNAME:-Administrator}"
CB_PASSWORD="${CB_PASSWORD:-password123}"
CB_BUCKET="${CB_BUCKET:-travel-sample}"
BIN=/opt/couchbase/bin

echo "==> Waiting for Couchbase REST API on ${CB_HOST}:8091 ..."
until curl -s -o /dev/null "http://${CB_HOST}:8091/pools"; do
  sleep 2
done

STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://${CB_HOST}:8091/pools/default")
if [ "$STATUS" = "200" ]; then
  echo "==> Cluster already initialized — skipping cluster-init."
else
  echo "==> Initializing cluster (services: data, index, query, fts) ..."
  "$BIN/couchbase-cli" cluster-init \
    --cluster "${CB_HOST}:8091" \
    --cluster-username "${CB_USERNAME}" \
    --cluster-password "${CB_PASSWORD}" \
    --cluster-ramsize 1024 \
    --cluster-index-ramsize 512 \
    --cluster-fts-ramsize 256 \
    --services data,index,query,fts \
    --cluster-name demo-cluster
fi

BUCKET_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -u "${CB_USERNAME}:${CB_PASSWORD}" \
  "http://${CB_HOST}:8091/pools/default/buckets/${CB_BUCKET}")
if [ "$BUCKET_STATUS" = "200" ]; then
  echo "==> Bucket '${CB_BUCKET}' already exists — skipping sample bucket install."
else
  echo "==> Installing native sample bucket '${CB_BUCKET}' ..."
  curl -s -u "${CB_USERNAME}:${CB_PASSWORD}" \
    "http://${CB_HOST}:8091/sampleBuckets/install" \
    -H 'Content-Type: application/json' \
    -d "[\"${CB_BUCKET}\"]"
  echo

  echo "==> Waiting for '${CB_BUCKET}' bucket to appear ..."
  until curl -s -o /dev/null -w "%{http_code}" -u "${CB_USERNAME}:${CB_PASSWORD}" \
    "http://${CB_HOST}:8091/pools/default/buckets/${CB_BUCKET}" | grep -q 200; do
    sleep 2
  done

  echo "==> Waiting for sample data to finish loading ..."
  while curl -s -u "${CB_USERNAME}:${CB_PASSWORD}" "http://${CB_HOST}:8091/pools/default/tasks" \
    | grep -q '"type":"loadingSampleBucket"'; do
    sleep 3
  done
fi

echo "==> Waiting for bucket + query service to settle ..."
sleep 8

echo "==> Creating primary index on '${CB_BUCKET}' (if missing) ..."
"$BIN/cbq" -engine="http://${CB_HOST}:8091" -u="${CB_USERNAME}" -p="${CB_PASSWORD}" \
  -script="CREATE PRIMARY INDEX ON \`${CB_BUCKET}\`;" \
  2>&1 | grep -v "already exists" || true

# ---------------------------------------------------------------------------
# Query monitoring: log every SQL++ request to system:completed_requests,
# with no cap on how many are retained.
#   completed-threshold=0  -> log requests regardless of execution time
#                             (default is 1000ms; 0 logs everything, -1 would
#                             log nothing)
#   completed-limit=4000   -> retain up to 4000 completed requests
#                             (-1 would mean no limit; 0 would track none)
# Docs:
#   https://docs.couchbase.com/server/current/n1ql/n1ql-manage/monitoring-n1ql-query.html#completed-threshold
#   https://docs.couchbase.com/server/current/n1ql/n1ql-manage/monitoring-n1ql-query.html#completed-limit
# Note this hits the Query Admin REST API on port 8093, not the cluster-admin
# REST API on 8091 used above.
# ---------------------------------------------------------------------------
echo "==> Configuring query monitoring (completed-threshold=0, completed-limit=4000) ..."
curl -s -u "${CB_USERNAME}:${CB_PASSWORD}" "http://${CB_HOST}:8093/admin/settings" \
  -H 'Content-Type: application/json' \
  -d '{"completed-threshold": 0, "completed-limit": 4000}'
echo   # curl doesn't print a trailing newline; keep log output tidy

echo "✅ Couchbase demo cluster is ready (bucket: ${CB_BUCKET})."

