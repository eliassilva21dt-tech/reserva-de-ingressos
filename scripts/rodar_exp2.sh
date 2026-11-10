#!/usr/bin/env bash
# Experimento 2: 1, 2 e 3 réplicas da API (MODO_LOCK=redis).
# Em vez de rodar sempre 1 -> 2 -> 3, percorre as 6 ordens possíveis: cada
# configuração aparece 2 vezes em cada posição, então um efeito do tipo
# "a máquina esquentou" não favorece nenhuma delas.
# Padrão: 6 blocos x 1 rodada = 6 repetições por configuração.
# Em cada configuração rodam duas cargas: "leve" (a do enunciado) e "forte"
# (gerador com vários processos, para a carga chegar de verdade à API).
# Cada rodada faz o próprio aquecimento e mede CPU durante a carga
# (ver carga_escala.py). Rodar da raiz do repositório.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
BLOCOS="${BLOCOS:-6}"
ORDENS=("1 2 3" "3 2 1" "2 3 1" "1 3 2" "3 1 2" "2 1 3")

for b in $(seq 1 "$BLOCOS"); do
  ordem="${ORDENS[$(( (b - 1) % 6 ))]}"
  echo "##### bloco $b: $ordem #####"
  for n in $ordem; do
    echo "=== $n réplica(s) da API ==="
    # --wait: só volta quando as réplicas estão saudáveis.
    MODO_LOCK=redis docker compose up -d --wait --scale api="$n" api proxy
    bash scripts/aguardar_api.sh
    sleep 7   # nginx re-resolve o DNS a cada 5 s (resolver valid=5s)
    # Carga leve (a do enunciado): 500 reservas, 50 conexões, 1 processo gerador.
    "$PY" scripts/carga_escala.py --replicas "$n" --rep "$b" --rotulo leve
    # Carga forte: 1000 reservas, 96 conexões, 8 processos geradores
    # (um processo só não consegue ocupar a API; ver docs/decisoes.md).
    "$PY" scripts/carga_escala.py --replicas "$n" --rep "$b" --rotulo forte \
          --reqs 1000 --conc 96 --processos "${PROCESSOS:-8}"
  done
done

echo
"$PY" scripts/resumir.py exp2
