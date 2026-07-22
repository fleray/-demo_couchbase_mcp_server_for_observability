#!/bin/sh
CB_HOST="couchbase"
CB_USERNAME="Administrator"
CB_PASSWORD="password123"
CB_BUCKET="demo"

echo "Starting light demo workload against ${CB_BUCKET} ..."
i=0
while true; do
  i=$((i + 1))
  ts=$(date +%s)

  curl -s -u "${CB_USERNAME}:${CB_PASSWORD}" \
    "http://${CB_HOST}:8093/query/service" \
    --data-urlencode "statement=UPSERT INTO \`${CB_BUCKET}\` (KEY, VALUE) VALUES ('doc-${i}', {\"n\":${i},\"ts\":${ts}})" \
    > /dev/null

  curl -s -u "${CB_USERNAME}:${CB_PASSWORD}" \
    "http://${CB_HOST}:8093/query/service" \
    --data-urlencode "statement=SELECT COUNT(*) AS cnt FROM \`${CB_BUCKET}\`" \
    > /dev/null

  sleep 2
done
