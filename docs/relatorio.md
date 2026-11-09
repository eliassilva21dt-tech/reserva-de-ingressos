# Relatório técnico — Reserva de Ingressos com Controle de Concorrência

*Rascunho: tabelas e resultados preenchidos; as interpretações precisam de revisão do grupo.*

## 1. Problema e objetivo
Vender o mesmo assento duas vezes (overbooking) quando várias réplicas da API recebem pedidos simultâneos. Objetivo: reservar assentos com consistência sob concorrência, pagamento assíncrono, e demonstrar com experimentos o efeito do lock distribuído e das restrições do banco.

## 2. Arquitetura e componentes
Ver diagrama em `docs/diagrama.png` (fonte editável: `docs/diagrama.svg`) e a seção 3 do `PROJETO.md`.
`proxy` (nginx) → `api` (FastAPI, N réplicas) → Redis (lock) / Postgres (dados) → RabbitMQ → `pagamento` (consumidor).

## 3. Comunicação entre componentes
- Cliente → nginx → API: REST/HTTP.
- API → pagamento: fila durável `reservas.pagamento` (mensagens persistentes, ack manual, prefetch 1).
- API/pagamento → Postgres: SQL (asyncpg). API/pagamento → Redis: comandos de lock.

## 4. Como executar
Ver `README.md`.

## 5. Persistência
Postgres com volume nomeado `pgdata`; Redis guarda só locks temporários (TTL 30 s).

## 6. Experimento 1 — Concorrência no mesmo assento
Hipótese, procedimento e comandos: `PROJETO.md` §10. Executar `bash scripts/rodar_exp1.sh`.

100 requisições simultâneas ao mesmo assento; média de 3 repetições (dados brutos: `evidencias/exp1/`).

| Modo | 201 (aceitas) | 409 | Erros | Lat. média (ms) | Lat. p95 (ms) | Overbooking? |
|---|---|---|---|---|---|---|
| none | 18,7 | 81,3 | 0 | 538,5 | 745,6 | **Sim (3/3 rodadas)** |
| db | 1 | 99 | 0 | 463,4 | 771,7 | Não |
| redis | 1 | 99 | 0 | 313,8 | 582,6 | Não |

**Interpretação (rascunho — o grupo deve revisar):** sem controle (`none`), várias réplicas passam na verificação "assento livre?" antes de qualquer INSERT e gravam a mesma vaga: houve overbooking nas 3 rodadas. Com `db` a restrição única do Postgres rejeita as duplicatas (só 1 aceita), mas as 99 perdedoras chegam até o banco. Com `redis` o `SET NX` barra as perdedoras antes do banco e a latência média foi a menor (~314 ms vs ~463 ms no `db`). Ressalva: 3 repetições, uma só máquina.
*Nota:* `CHECK_INSERT_DELAY_MS` (50 ms) existe só para ampliar a janela da condição de corrida no modo `none` de forma reprodutível.

## 7. Experimento 2 — Escalabilidade da API
Executar `bash scripts/rodar_exp2.sh` (500 reservas em assentos distintos, 50 conexões simultâneas).

Média de 3 repetições (dados brutos: `evidencias/exp2/`). As 500 reservas foram aceitas (201) em todas as rodadas.

| Réplicas | Tempo total (s) | Req/s | Lat. média (ms) | Lat. p95 (ms) | Distribuição por instância (última rep.) |
|---|---|---|---|---|---|
| 1 | 4,0 | 131,8 | 379,9 | 1061,1 | 500 |
| 2 | 5,4 | 93,4 | 513,8 | 1337,9 | 258 / 242 |
| 3 | 5,4 | 92,2 | 519,3 | 1414,1 | 155 / 176 / 169 |

**Interpretação (honesta, rascunho):** o balanceamento funciona (carga dividida quase igualmente entre as instâncias), mas **mais réplicas não aumentaram a vazão**: ficou em ~92 req/s com 2 e 3 réplicas, e com 1 réplica variou muito entre rodadas (97 a 156 req/s). A CPU dos contêineres ficou quase ociosa (docker stats), então o gargalo não é a API: provável combinação de gerador de carga em Python, rede host↔WSL2 do Docker Desktop, e Postgres/Redis/pagamento compartilhando a mesma máquina. Para afirmar a causa seria preciso medir (ex.: rodar o gerador de carga dentro da rede Docker).

## 8. Experimento de falha — queda do pagamento-service
Executar `bash scripts/falha_pagamento.sh` e `bash scripts/falha_pagamento.sh durante`.
**Comportamento observado** (logs em `evidencias/falha/`):
- *Parada com fila cheia:* com o pagamento parado, a API continuou aceitando: 20 reservas `201 PENDENTE`; `rabbitmqctl` mostrou `messages_ready=20, unacked=0`. Ao subir o pagamento, a fila foi a 0 em ~40 s e as 20 reservas viraram `CONFIRMADA`. Nenhuma perdida.
- *Queda durante o processamento:* a mensagem em curso aparecia como `unacked=1`; após matar o contêiner voltou para `ready` e foi reprocessada com a marca `(REENTREGA)` no log. Resultado final: 18 `CONFIRMADA` + 2 `RECUSADA`, nenhuma perdida.

## 9. Evidências
Pasta `evidencias/` (JSON das cargas, logs, saída do `rabbitmqctl`, prints do painel `:15672`).

## 10. Limitações e melhorias
- Redis é ponto único de falha; RabbitMQ e Postgres sem réplica.
- Lock com TTL pode expirar antes do fim da operação (o índice único do Postgres continua protegendo).
- Pagamento é simulado.
- Testes em uma única máquina.
- Melhorias: Redis/Postgres replicados, Redlock, Kubernetes, tracing distribuído, Prometheus/Grafana.
