#!/usr/bin/env bash
# Experimento 1: 3 modos, em 3 blocos com a ordem trocada (quadrado latino),
# para que nenhum modo rode sempre primeiro ou sempre por último.
# Padrão: 3 blocos x 2 repetições = 6 repetições por modo.
# Cada rodada faz o próprio aquecimento (ver carga_concorrencia.py).
# Rodar da raiz do repositório.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
REPS_POR_BLOCO="${REPS_POR_BLOCO:-2}"
ORDENS=("none db redis" "redis none db" "db redis none")

rep=0
for ordem in "${ORDENS[@]}"; do
  echo "##### bloco: $ordem #####"
  for modo in $ordem; do
    echo "=== MODO_LOCK=$modo ==="
    MODO_LOCK="$modo" docker compose up -d --wait --force-recreate api
    bash scripts/aguardar_api.sh
    sleep 7   # nginx re-resolve o DNS a cada 5 s (resolver valid=5s)
    for i in $(seq 1 "$REPS_POR_BLOCO"); do
      echo "--- bloco com início em rep $((rep + 1)), rodada $i ---"
      "$PY" scripts/carga_concorrencia.py --rep "$((rep + i))"
    done
  done
  rep=$((rep + REPS_POR_BLOCO))
done

# Deixa a stack no modo padrão (redis) ao terminar.
MODO_LOCK=redis docker compose up -d --wait --force-recreate api
echo
"$PY" scripts/resumir.py exp1
