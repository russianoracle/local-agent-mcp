#!/bin/bash
# Check oMLX health and start if not running
OMLX_URL="${OMLX_URL:-http://localhost:8000}"
OMLX_API_KEY="${OMLX_API_KEY:-}"
if ! curl -sf -H "Authorization: Bearer ${OMLX_API_KEY}" "${OMLX_URL}/health" > /dev/null 2>&1; then
    echo "Starting oMLX..."
    omlx start
    sleep 3
fi
echo "oMLX ready at ${OMLX_URL}"
