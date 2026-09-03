#!/bin/bash
if [ -f .env ]; then
  set -a; source .env; set +a
fi

# Use .env variables or fall back to defaults
IQ_URL="${AEROLAKE_IQENGINE_URL:-http://localhost:3000}"
BASE_URL="${IQ_URL%/}/api/v1/integration"
ACCOUNT="${AEROLAKE_IQENGINE_ACCOUNT:-aerolake}"
CONTAINER="${AEROLAKE_IQENGINE_CONTAINER:-aerolake-captures}"


echo "Triggering initial catalog sync..."

# 1. Trigger the sync and parse the job_id
RESPONSE=$(curl -s -X POST "$BASE_URL/datasources/$ACCOUNT/$CONTAINER/sync")
JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('job_id', ''))")

if [ -z "$JOB_ID" ]; then
  echo "Failed to start sync. API Response: $RESPONSE"
  exit 1
fi

echo "Job $JOB_ID queued. Waiting for indexing to finish..."

# 2. Poll the status endpoint every 2 seconds
while true; do
  STATUS_RESPONSE=$(curl -s "$BASE_URL/sync/$JOB_ID")
  STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))")
  
  if [ "$STATUS" = "completed" ]; then
    echo -e "\nSync completed successfully!"
    break
  elif [ "$STATUS" = "failed" ]; then
    echo -e "\nSync failed. API Response: $STATUS_RESPONSE"
    exit 1
  fi
  
  echo -n "."
  sleep 2
done