# Relatório técnico — Reserva de Ingressos com Controle de Concorrência

*Rascunho completo: todas as seções, tabelas e evidências estão preenchidas; o grupo deve revisar as interpretações e reescrever com as próprias palavras.*

## 1. Problema e objetivo
Vender o mesmo assento duas vezes (overbooking) quando várias réplicas da API recebem pedidos simultâneos. Objetivo: reservar assentos com consistência sob concorrência, pagamento assíncrono, e demonstrar com experimentos o efeito do lock distribuído e das restrições do banco.

## 2. Arquitetura e componentes
Ver diagrama em `docs/diagrama.png` (fonte editável: `docs/diagrama.svg`) e a seção 3 do `PROJETO.md`.
`proxy` (nginx) → `api` (FastAPI, N réplicas) → Redis (lock) / Postgres (dados) → RabbitMQ → `pagamento` (consumidor).

## 3. Comunicação entre componentes

Há dois estilos de comunicação, escolhidos de propósito: **síncrona** (REST) onde o cliente precisa de resposta imediata e **assíncrona** (fila) onde o trabalho pode acontecer depois.

| Origem → destino | Meio | Detalhes |
|---|---|---|
| Cliente → nginx → API | REST/HTTP | `GET /health`, `GET /eventos/{id}/assentos`, `POST /reservas`, `GET /reservas/{id}`, mais `/admin/reset` e `/admin/stats` (só em desenvolvimento). O nginx reparte as requisições entre as réplicas da API e re-resolve o DNS a cada 5 s, então `--scale api=N` funciona sem reiniciar o proxy. |
| API → pagamento | Fila RabbitMQ `reservas.pagamento` | Fila **durável**, mensagens **persistentes** com corpo `{"reserva_id", "assento_id"}`. O consumidor usa **ack manual** (só confirma depois de atualizar o banco) e `prefetch=1`. Se ele cair no meio, a mensagem é reentregue. A atualização é **idempotente**: `UPDATE ... WHERE status = 'PENDENTE'`. |
| API → Redis | Comandos Redis | Lock por assento com `SET lock:assento:{id} <token> NX EX 30`. A API libera o lock com um script Lua (apaga só se o token for o seu). |
| pagamento → Redis | Comando `DEL` | Quando o pagamento é recusado, apaga o lock e o assento volta a ficar livre. |
| API e pagamento → Postgres | SQL (asyncpg) | A API grava a reserva `PENDENTE`; o pagamento a atualiza para `CONFIRMADA` ou `RECUSADA`. |

**Redes Docker.** `front-net` liga o `proxy` à `api`; `back-net` liga `api`, `pagamento`, `redis`, `rabbitmq` e `db`. Só o `proxy` publica porta no host (8080); o painel do RabbitMQ (15672) fica exposto apenas para desenvolvimento.

**Tolerância a falhas na comunicação.** API e pagamento reconectam ao RabbitMQ e ao Postgres com tentativas e espera crescente na subida. Se Redis, RabbitMQ ou Postgres estiverem fora, a API responde `503` (testado com o Redis parado). Se a publicação na fila falhar, a API desfaz a reserva e responde `503`, para não deixar uma reserva `PENDENTE` que ninguém vai processar.

## 4. Como executar

Pré-requisitos: Docker Desktop e Python 3.12+ (só para os scripts de carga).

```bash
cp .env.example .env
docker compose up -d --build
bash scripts/aguardar_api.sh
curl http://localhost:8080/health
```

Os comandos de cada experimento, como trocar o modo de controle e como parar e limpar estão no `README.md`; o `GUIA.md` traz o passo a passo completo e um roteiro de demonstração. A stack sobe com 2 réplicas da API, e o RabbitMQ tem painel em http://localhost:15672 (guest/guest).

## 5. Persistência

O Postgres guarda eventos, assentos e reservas em um volume nomeado (`pgdata`). O Redis guarda apenas locks temporários (TTL de 30 s) e o RabbitMQ usa fila durável com mensagens persistentes. Verificado: após `docker compose down` e `up` (sem `-v`) os 1000 assentos e as reservas continuam no banco. `docker compose down -v` apaga o volume.

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

Prints do painel do RabbitMQ para o cenário "pagamento parado" (em `evidencias/falha/`, descritos em `PRINTS_RABBITMQ.md`): fila vazia; 20 mensagens prontas com 0 consumidores; drenagem com 1 `unacked`; fila zerada, com o gráfico descendo de 20 para 0. Nessa execução as 20 reservas terminaram em 19 `CONFIRMADA` e 1 `RECUSADA`, nenhuma perdida.

## 9. Evidências

Tudo fica na pasta `evidencias/`, versionada junto com o código.

| Pasta | Conteúdo |
|---|---|
| `evidencias/exp1/` | JSON de cada rodada do Experimento 1 (3 modos × 3 repetições) e o estado da stack coletado (`compose ps`, logs da API, do pagamento e do proxy, `rabbitmqctl`, overbooking). |
| `evidencias/exp2/` | JSON de cada rodada do Experimento 2 (1, 2 e 3 réplicas × 3 repetições) e o estado coletado. |
| `evidencias/falha/` | Logs completos das duas variantes do experimento de falha (`falha_normal_*.log` e `falha_durante_*.log`, com a marca `REENTREGA`), o estado coletado e 4 prints do painel do RabbitMQ. |

As tabelas deste relatório são geradas por `python scripts/resumir.py exp1` e `exp2` a partir desses JSON. O CI do GitHub Actions roda um teste de fumaça (sobe a stack e confirma que 20 pedidos ao mesmo assento resultam em exatamente 1 reserva).

## 10. Limitações e melhorias
- Redis é ponto único de falha; RabbitMQ e Postgres sem réplica. Com o Redis fora, o modo `redis` devolve `503`.
- Lock com TTL pode expirar antes do fim da operação (o índice único do Postgres continua protegendo).
- O pagamento apaga o lock com `DEL` simples, sem conferir o token (o contrato da mensagem só leva `reserva_id` e `assento_id`).
- Pagamento é simulado.
- Testes em uma única máquina (Windows com Docker Desktop/WSL2): o resultado de escalabilidade não é generalizável.
- `docker kill` conta como parada manual e não reinicia o contêiner; a política `unless-stopped` só reinicia em queda real (verificado).
- Melhorias: Redis/Postgres replicados, Redlock, Kubernetes, tracing distribuído, Prometheus/Grafana, e repetir o Experimento 2 com o gerador de carga dentro da rede Docker.

## 11. Conclusão

O projeto mostra, com dados, que a verificação "está livre? então reserva" não é segura quando há várias réplicas: sem controle, o Experimento 1 aceitou em média 18,7 reservas para o mesmo assento nas 3 rodadas. O índice único do Postgres e o lock no Redis resolveram o problema (exatamente 1 reserva aceita), e o Redis foi o mais rápido (~314 ms contra ~463 ms) porque barra os perdedores antes de chegar ao banco. Manter os dois juntos dá defesa em camadas: o lock reduz a carga e o índice garante a consistência mesmo se o lock expirar.

O pagamento assíncrono por fila durável com ack manual e atualização idempotente funcionou como esperado: com o serviço parado, a API continuou aceitando reservas, a fila acumulou as mensagens e, ao religar, todas foram processadas, sem perda e com reentrega quando o consumidor foi morto no meio do trabalho.

O Experimento 2 não mostrou ganho de vazão com mais réplicas nesta máquina. Isso não invalida a arquitetura (a API é sem estado e o balanceamento divide a carga quase por igual), mas indica que o gargalo estava fora da API; confirmar a causa exige a medição sugerida nas limitações.
