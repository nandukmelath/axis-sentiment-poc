#!/usr/bin/env bash
# Smoke-test every read endpoint of the running API (default http://127.0.0.1:8600).
# Usage: bash tools/smoke_api.sh [BASE_URL]
BASE="${1:-http://127.0.0.1:8600}"
fail=0
for ep in health ready kpis clusters competitor-sov alerts churn products forecast entities cost; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 20 "$BASE/$ep")
  echo "  /$ep -> $code"
  [ "$code" = "200" ] || fail=1
done
exit $fail
