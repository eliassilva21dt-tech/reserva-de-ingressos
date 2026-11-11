#!/usr/bin/env bash
# Teste de falha: as mensagens que estão na fila sobrevivem a um
# "docker compose down" seguido de "up" (reinício da stack inteira)?
#
#   bash scripts/falha_reinicio.sh [rotulo]
#
# O rótulo só entra no nome do log (ex.: sem_volume, com_volume).
# Saída completa salva em evidencias/falha/.
set -uo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE_URL:-http://localhost:8080}"
N="${N:-20}"
ROTULO="${1:-teste}"
DIR="evidencias/falha"
mkdir -p "$DIR"
LOG="$DIR/reinicio_${ROTULO}_$(date +%Y%m%d-%H%M%S).log"
exec > >(tee "$LOG") 2>&1

fila() {
  docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged 2>&1 \
    | grep -E 'reservas.pagamento' || echo "(a fila reservas.pagamento não existe ou está inacessível)"
}
status() {
  docker compose exec -T db psql -U app -d ingressos -At \
    -c "SELECT status || '=' || count(*) FROM reservas GROUP BY status ORDER BY status" | tr '\n' ' '
  echo
}
pendentes() {
  docker compose exec -T db psql -U app -d ingressos -At \
    -c "SELECT count(*) FROM reservas WHERE status = 'PENDENTE'" | tr -d '[:space:]'
}
passo() { echo; echo "### $(date +%H:%M:%S) $*"; }

passo "1. reset"
curl -fsS -X POST "$BASE/admin/reset"; echo

passo "2. parando o pagamento-service (para as mensagens ficarem na fila)"
docker compose stop pagamento

passo "3. criando $N reservas (assentos 1..$N)"
for i in $(seq 1 "$N"); do
  curl -sS -o /dev/null -w "%{http_code} " -X POST "$BASE/reservas" -H 'Content-Type: application/json' \
       -d "{\"assento_id\": $i, \"cliente\": \"reinicio$i\"}"
done
echo

passo "4. antes do reinício: fila (prontas / unacked) e reservas por status"
fila
status

passo "5. docker compose down (sem -v: os volumes nomeados são mantidos)"
docker compose down

passo "6. subindo só db, redis e rabbitmq (sem API e sem pagamento, para ninguém mexer na fila)"
docker compose up -d --wait db redis rabbitmq

passo "7. depois do reinício: fila e reservas por status (esperado: $N mensagens prontas e $N PENDENTE)"
fila
status

passo "8. subindo o resto da stack (API, pagamento, proxy)"
docker compose up -d --wait
bash scripts/aguardar_api.sh

passo "9. aguardando o pagamento processar as reservas (até 90 s)"
for t in $(seq 1 45); do
  P=$(pendentes)
  [ "$P" = "0" ] && break
  sleep 2
done
fila
status

passo "10. resultado"
P=$(pendentes)
if [ "$P" = "0" ]; then
  echo "RESULTADO: nenhuma reserva perdida. As $N mensagens sobreviveram ao reinício e foram processadas."
else
  echo "RESULTADO: $P reservas ficaram PENDENTE sem mensagem na fila. As mensagens se perderam no reinício e esses assentos ficam presos."
fi
echo "log salvo em $LOG"
