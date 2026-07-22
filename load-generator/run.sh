#!/bin/bash
set -e

# Load environment variables from .env if it exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Default values
CB_HOST="${CB_HOST:-couchbase}"
CB_USERNAME="${CB_USERNAME:-Administrator}"
CB_PASSWORD="${CB_PASSWORD:-password123}"
CB_BUCKET="${CB_BUCKET:-travel-sample}"
FLASK_PORT="${FLASK_PORT:-5000}"

echo "========================================="
echo "Couchbase Load Generator"
echo "========================================="
echo ""
echo "Configuration:"
echo "  Couchbase Host: $CB_HOST"
echo "  Bucket: $CB_BUCKET"
echo "  Flask Port: $FLASK_PORT"
echo ""
echo "Starting Flask application..."
echo ""

export FLASK_APP=app.py
export PYTHONUNBUFFERED=1

python app.py
