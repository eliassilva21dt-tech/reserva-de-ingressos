#!/usr/bin/env bash
# Coleta o estado da stack em evidencias/<experimento>/.
#   bash scripts/coletar_evidencias.sh exp1
set -uo pipefail
cd "$(dirname "$0")/.."

EXP="${1:?uso: coletar_evidencias.sh <exp1|exp2|falha>}"
BASE="${BASE_URL:-http://localhost:8080}"
DIR="evidencias/$EXP"
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$DIR"

docker compose ps                         > "$DIR/${TS}_compose_ps.txt" 2>&1
docker compose logs --no-color --tail=300 api        > "$DIR/${TS}_logs_api.txt" 2>&1
docker compose logs --no-color --tail=300 pagamento  > "$DIR/${TS}_logs_pagamento.txt" 2>&1
docker compose logs --no-color --tail=300 proxy      > "$DIR/${TS}_logs_proxy.txt" 2>&1
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged \
                                          > "$DIR/${TS}_rabbit_filas.txt" 2>&1
curl -sS "$BASE/admin/stats"              > "$DIR/${TS}_stats.json" 2>&1
docker compose exec -T db psql -U app -d ingressos -c \
  "SELECT assento_id, count(*) AS reservas_ativas FROM reservas WHERE status IN ('PENDENTE','CONFIRMADA') GROUP BY assento_id HAVING count(*) > 1;" \
                                          > "$DIR/${TS}_overbooking.txt" 2>&1

echo "evidências salvas em $DIR (prefixo $TS)"
echo "Lembre de tirar prints do painel do RabbitMQ: http://localhost:15672 (guest/guest)"
