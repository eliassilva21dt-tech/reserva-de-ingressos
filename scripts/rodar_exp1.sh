#!/usr/bin/env bash
# Experimento 1: 3 modos x 3 repetições. Rodar da raiz do repositório.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
REPS="${REPS:-3}"

for modo in none db redis; do
  echo "=== MODO_LOCK=$modo ==="
  MODO_LOCK="$modo" docker compose up -d --force-recreate api
  bash scripts/aguardar_api.sh
  sleep 3   # dá tempo ao DNS do nginx (valid=5s) enxergar as novas réplicas
  for rep in $(seq 1 "$REPS"); do
    echo "--- repetição $rep ---"
    "$PY" scripts/carga_concorrencia.py --rep "$rep"
  done
done

# Deixa a stack no modo padrão (redis) ao terminar.
MODO_LOCK=redis docker compose up -d --force-recreate api
echo
"$PY" scripts/resumir.py exp1
