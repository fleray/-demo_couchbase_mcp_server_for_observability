#!/bin/bash
set -e

CB_HOST="${CB_HOST:-couchbase}"
CB_BUCKET="${CB_BUCKET:-travel-sample}"

echo "========================================="
echo "Couchbase Load Generator (cbc-n1qlback)"
echo "========================================="
echo ""
echo "Configuration:"
echo "  Couchbase Host: $CB_HOST"
echo "  Bucket: $CB_BUCKET"
echo ""
echo "Starting web view on :5000 ..."
echo ""

exec python3 app-new.py
