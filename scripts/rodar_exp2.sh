#!/usr/bin/env bash
# Experimento 2: 1, 2 e 3 réplicas da API x 3 repetições (MODO_LOCK=redis).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
REPS="${REPS:-3}"

for n in 1 2 3; do
  echo "=== $n réplica(s) da API ==="
  # Se o modo atual não for redis, a API é recriada; se já for, só ajusta o nº de réplicas.
  MODO_LOCK=redis docker compose up -d --scale api="$n" api proxy
  bash scripts/aguardar_api.sh
  sleep 7   # nginx re-resolve o DNS a cada 5 s (resolver valid=5s)
  for rep in $(seq 1 "$REPS"); do
    echo "--- repetição $rep ---"
    "$PY" scripts/carga_escala.py --replicas "$n" --rep "$rep"
  done
done

echo
"$PY" scripts/resumir.py exp2
