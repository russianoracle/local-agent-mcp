#!/bin/bash
# Check oMLX health and start if not running
if ! curl -sf -H "Authorization: Bearer 1986" http://localhost:8000/health > /dev/null 2>&1; then
    echo "Starting oMLX..."
    omlx start
    sleep 3
fi
echo "oMLX ready at :8000"
