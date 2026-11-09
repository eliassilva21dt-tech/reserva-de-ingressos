#!/usr/bin/env bash
# Experimento de falha: queda do pagamento-service.
#
#   bash scripts/falha_pagamento.sh            # parada com a fila cheia (padrão)
#   bash scripts/falha_pagamento.sh durante    # mata o pagamento DURANTE o processamento (reentrega)
#
# Saída completa (também salva em evidencias/falha/).
set -uo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE_URL:-http://localhost:8080}"
N="${N:-20}"
VARIANTE="${1:-normal}"
DIR="evidencias/falha"
mkdir -p "$DIR"
LOG="$DIR/falha_${VARIANTE}_$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1

fila() { docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged | grep -E 'name|reservas.pagamento'; }
passo() { echo; echo "### $(date +%H:%M:%S) $*"; }

passo "1. reset"
curl -fsS -X POST "$BASE/admin/reset"; echo

passo "2. parando o pagamento-service"
if [ "$VARIANTE" = "durante" ]; then
  : # só será derrubado depois que ele pegar uma mensagem (passo 3b)
else
  docker compose stop pagamento
fi

passo "3. criando $N reservas (assentos 1..$N)"
IDS=()
for i in $(seq 1 "$N"); do
  RESP=$(curl -sS -X POST "$BASE/reservas" -H 'Content-Type: application/json' \
         -d "{\"assento_id\": $i, \"cliente\": \"falha$i\"}")
  ID=$(echo "$RESP" | sed -n 's/.*"id":\([0-9]*\).*/\1/p')
  IDS+=("$ID")
  echo "$RESP"
done

if [ "$VARIANTE" = "durante" ]; then
  passo "3b. aguardando o pagamento pegar uma mensagem (fica 'unacked') e matando-o"
  sleep 1
  fila
  docker compose kill pagamento
  sleep 2
fi

passo "4. estado da fila (normal: ~$N prontas, 0 unacked; durante: restantes prontas, 0 unacked = a mensagem em curso voltou para a fila)"
fila

passo "5. status das reservas (normal: todas PENDENTE; durante: algumas já CONFIRMADA antes da queda)"
for id in "${IDS[@]}"; do curl -sS "$BASE/reservas/$id" | grep -o '"status":"[A-Z]*"'; done | sort | uniq -c

passo "6. subindo o pagamento-service"
docker compose start pagamento

passo "7. aguardando a fila esvaziar (timeout 300 s)"
for t in $(seq 1 150); do
  LINHA=$(fila | grep reservas.pagamento || true)
  PRONTAS=$(echo "$LINHA" | awk '{print $2}')
  UNACK=$(echo "$LINHA" | awk '{print $3}')
  echo "$(date +%H:%M:%S) prontas=${PRONTAS:-?} unacked=${UNACK:-?}"
  if [ "${PRONTAS:-1}" = "0" ] && [ "${UNACK:-1}" = "0" ]; then break; fi
  sleep 2
done

passo "8. status final das reservas (esperado: CONFIRMADA/RECUSADA, nenhuma perdida)"
for id in "${IDS[@]}"; do curl -sS "$BASE/reservas/$id" | grep -o '"status":"[A-Z]*"'; done | sort | uniq -c

passo "9. logs do pagamento (note REENTREGA na variante 'durante')"
docker compose logs --no-log-prefix --tail=60 pagamento

echo
echo "log salvo em $LOG"
