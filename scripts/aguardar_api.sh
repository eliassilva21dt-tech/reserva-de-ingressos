#!/usr/bin/env bash
# Espera a API responder em /health através do proxy (até ~60 s).
BASE="${BASE_URL:-http://localhost:8080}"
for i in $(seq 1 60); do
  if curl -fsS "$BASE/health" >/dev/null 2>&1; then
    echo "API respondendo."
    exit 0
  fi
  sleep 1
done
echo "API não respondeu em 60 s." >&2
exit 1
