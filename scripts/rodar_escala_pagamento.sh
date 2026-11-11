#!/usr/bin/env bash
# Experimento extra: 1, 2 e 4 consumidores do pagamento-service.
# 3 blocos com a ordem trocada = 3 repetições por configuração (~9 min).
# Rodar da raiz do repositório.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
ORDENS=("1 2 4" "4 1 2" "2 4 1")

rep=0
for ordem in "${ORDENS[@]}"; do
  rep=$((rep + 1))
  echo "##### bloco $rep: $ordem #####"
  for n in $ordem; do
    echo "=== $n consumidor(es) ==="
    docker compose up -d --scale pagamento="$n" pagamento
    sleep 6   # tempo para os consumidores se conectarem à fila
    "$PY" scripts/escala_pagamento.py --consumidores "$n" --rep "$rep"
  done
done

# Volta ao padrão (1 consumidor).
docker compose up -d --scale pagamento=1 pagamento
echo
"$PY" scripts/resumir.py pagamento
