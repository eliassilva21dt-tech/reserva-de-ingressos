# Relatório técnico — Reserva de Ingressos com Controle de Concorrência

## 1. Problema e objetivo
Vender o mesmo assento duas vezes (overbooking) quando várias réplicas da API recebem pedidos simultâneos. Objetivo: reservar assentos com consistência sob concorrência, pagamento assíncrono, e demonstrar com experimentos o efeito do lock distribuído e das restrições do banco.

## 2. Arquitetura e componentes
Ver diagrama em `docs/diagrama.png` (fonte editável: `docs/diagrama.svg`) e a seção 3 do `PROJETO.md`.
`proxy` (nginx) → `api` (FastAPI, N réplicas) → Redis (lock) / Postgres (dados) → RabbitMQ → `pagamento` (consumidor).

## 3. Comunicação entre componentes

Há dois estilos de comunicação, escolhidos de propósito: **síncrona** (REST) onde o cliente precisa de resposta imediata e **assíncrona** (fila) onde o trabalho pode acontecer depois.

| Origem → destino | Meio | Detalhes |
|---|---|---|
| Cliente → nginx → API | REST/HTTP | `GET /health`, `GET /eventos/{id}/assentos`, `POST /reservas`, `GET /reservas/{id}`, mais `/admin/reset` e `/admin/stats` (só em desenvolvimento). As respostas `201` e `409` de `POST /reservas` levam o cabeçalho `Server-Timing` com o tempo gasto na API, usado nos experimentos. O nginx reparte as requisições entre as réplicas da API e re-resolve o DNS a cada 5 s, então `--scale api=N` funciona sem reiniciar o proxy. |
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

O Postgres guarda eventos, assentos e reservas em um volume nomeado (`pgdata`). O Redis guarda apenas locks temporários (TTL de 30 s). O RabbitMQ usa fila durável com mensagens persistentes e também tem volume nomeado (`rabbitmq_data`) e hostname fixo, para que as mensagens na fila sobrevivam a um reinício da stack. Verificado: após `docker compose down` e `up` (sem `-v`) os 1000 assentos e as reservas continuam no banco, e as mensagens pendentes continuam na fila (seção 8.1). `docker compose down -v` apaga os volumes.

## 6. Experimento 1 — Concorrência no mesmo assento
Hipótese, procedimento e comandos: `PROJETO.md` §10. Executar `bash scripts/rodar_exp1.sh`.

100 requisições simultâneas ao mesmo assento. Cada modo rodou **6 vezes**, em 3 blocos com a ordem trocada (`none db redis`, `redis none db`, `db redis none`), para nenhum modo rodar sempre primeiro. Antes de cada rodada há um aquecimento descartado (100 reservas em assentos diferentes). Valores: média ± desvio padrão entre as rodadas (dados brutos: `evidencias/exp1/`).

| Modo | 201 (aceitas) | 409 | Erros | Tempo na API para recusar (ms) | Tempo na API para aceitar (ms) | Overbooking? |
|---|---|---|---|---|---|---|
| none | 9,7 (de 4 a 17) | 90,3 | 0 | 1,9 ± 0,2 | 62,0 ± 2,0 | **Sim (6/6 rodadas)** |
| db | 1 | 99 | 0 | 1,7 ± 0,1 | 9,8 ± 1,7 | Não (0/6) |
| redis | 1 | 99 | 0 | 1,0 ± 0,3 | 10,6 ± 1,9 | Não (0/6) |

"Tempo na API" é medido dentro da própria API e devolvido no cabeçalho HTTP `Server-Timing`; não inclui proxy, rede nem o gerador de carga.

**Interpretação:** sem controle (`none`), várias requisições passam na verificação "assento livre?" antes de qualquer INSERT e gravam a mesma vaga: houve overbooking nas 6 rodadas, com 4 a 17 reservas aceitas para o mesmo assento. Com `db` e com `redis` foi aceita exatamente 1 reserva em todas as rodadas.

**Sobre velocidade:** não dá para dizer que um modo é mais rápido que o outro para o cliente. A latência vista pelo cliente variou de ~270 ms a ~1100 ms entre rodadas do mesmo modo (redis: 640 ± 398 ms; db: 669 ± 223 ms; none: 766 ± 305 ms), porque quem domina esse número é o gerador de carga disparando 100 pedidos de uma vez, e não o modo. Dentro da API, recusar um pedido custa de 1 a 2 ms nos três modos; o `redis` recusa em 1,0 ms e o `db` em 1,7 ms, uma diferença pequena. A vantagem do `redis` é outra: os 99 pedidos perdedores são barrados antes de chegar ao Postgres, enquanto no `db` cada um vira um INSERT que o banco precisa rejeitar. No modo `none`, aceitar leva ~62 ms por causa do atraso proposital de 50 ms.

*Nota:* `CHECK_INSERT_DELAY_MS` (50 ms) existe só para ampliar a janela da condição de corrida no modo `none` de forma reprodutível.

*Medição anterior:* a primeira bateria (3 repetições, sem aquecimento com reservas) está guardada em `evidencias/exp1/v1/`. Ela levou à afirmação "o Redis foi mais rápido (314 ms contra 463 ms)", que foi retirada: a diferença vinha da primeira rodada de cada modo, feita logo depois de recriar a API.

## 7. Experimento 2 — Escalabilidade da API
Executar `bash scripts/rodar_exp2.sh`. Modo `redis`, reservas em assentos distintos, com 1, 2 e 3 réplicas da API.

Cada configuração rodou **6 vezes**, percorrendo as 6 ordens possíveis de (1, 2, 3), para que nenhuma rode sempre primeiro. Cada rodada tem aquecimento descartado (150 reservas), confere se o número esperado de réplicas está respondendo e mede, além de vazão e latência: a **CPU de cada contêiner durante a carga** (contador de CPU do contêiner lido antes e depois; 100% = um núcleo inteiro) e o **tempo de cada etapa dentro da API** (cabeçalho `Server-Timing`). Todas as reservas foram aceitas (201) em todas as rodadas. Dados brutos: `evidencias/exp2/`.

Rodamos duas cargas, porque a primeira não chegou a ocupar a API.

### 7.1 Carga leve (a do enunciado): 500 reservas, 50 conexões, 1 processo gerando carga

| Réplicas | Req/s | Lat. média (ms) | Lat. p95 (ms) | Tempo dentro da API (ms) | CPU da API, por réplica | Distribuição (última rep.) |
|---|---|---|---|---|---|---|
| 1 | 102,3 ± 3,0 | 469 ± 9 | 1259 ± 52 | 27 ± 15 | 48% ± 3 | 500 |
| 2 | 99,4 ± 2,7 | 482 ± 13 | 1267 ± 75 | 17 ± 17 | 30% ± 2 | 266 / 234 |
| 3 | 100,3 ± 5,2 | 476 ± 25 | 1275 ± 84 | 13 ± 5 | 24% ± 2 | 180 / 161 / 159 |

**Interpretação:** a vazão ficou em ~100 req/s com 1, 2 ou 3 réplicas. Isso **não** mede a API: dos ~470 ms que o cliente espera, só 13 a 27 ms são gastos dentro da API, e a API usou menos da metade de um núcleo. Mais de 90% do tempo fica fora da API. Um único processo Python não consegue enviar pedidos rápido o bastante; quem limita o resultado é o **gerador de carga**. Numa checagem avulsa, o mesmo script rodado de dentro da rede Docker (sem passar pelo Windows) também ficou preso ao gerador, e o log do nginx mostrou ~14 ms por requisição.

### 7.2 Carga forte: 1000 reservas, 96 conexões, 8 processos gerando carga

| Réplicas | Req/s | Ganho | Lat. média (ms) | Lat. p95 (ms) | CPU da API, por réplica | Distribuição (última rep.) |
|---|---|---|---|---|---|---|
| 1 | 259 ± 49 | — | 374 ± 93 | 694 ± 650 | 98% ± 11 | 1000 |
| 2 | 351 ± 61 | +35% | 271 ± 62 | 669 ± 566 | 95% ± 21 | 520 / 480 |
| 3 | 415 ± 14 | +60% | 220 ± 6 | 646 ± 134 | 93% ± 10 | 361 / 320 / 319 |

Req/s por rodada: 1 réplica: 269, 263, 312, 285, 169, 258 · 2 réplicas: 369, 388, 237, 406, 334, 375 · 3 réplicas: 424, 432, 403, 399, 424, 408.

**Interpretação:** com carga suficiente, cada réplica da API passa a usar quase um núcleo inteiro (93% a 98%) e a latência passa a ser gasta dentro da API (186 a 341 ms de um total de 220 a 374 ms). Agora a API é o gargalo, e **mais réplicas aumentam a vazão**: +35% com 2 réplicas e +60% com 3. O balanceamento divide a carga quase por igual.

O ganho fica longe do ideal (3 réplicas renderam 1,6 vez, e não 3 vezes). A causa não foi medida. A hipótese mais provável é a máquina única: os 8 processos do gerador, as réplicas da API, o Postgres e o RabbitMQ disputam os mesmos 12 núcleos lógicos. Reforça essa hipótese o fato de o custo de CPU por reserva na API subir de ~3,8 ms (1 réplica) para ~6,7 ms (3 réplicas). Com 1 e 2 réplicas a variação entre rodadas também foi grande (uma rodada de 169 e outra de 237 req/s). São 6 repetições em uma máquina só; os números valem para esta máquina.

*Medição anterior:* a primeira bateria (3 repetições, ordem fixa, só a carga leve) está em `evidencias/exp2/v1/`. Ela levou a duas afirmações que foram retiradas: "a CPU ficou quase ociosa, então a API não é o gargalo" (o `docker stats` tinha sido tirado depois do fim da carga) e "com 1 réplica a vazão foi maior" (diferença dentro da variação entre rodadas).

### 7.3 Experimento extra — escalar o pagamento-service
Executar `bash scripts/rodar_escala_pagamento.sh`. Com 1, 2 e 4 consumidores (`--scale pagamento=N`), criamos 40 reservas de uma vez e medimos o tempo até todas saírem de `PENDENTE`. 3 repetições por configuração, com a ordem trocada a cada bloco; o script confere no RabbitMQ se o número de consumidores é o esperado. Dados brutos: `evidencias/escala_pagamento/`.

| Consumidores | Tempo para processar 40 pagamentos (s) | Pagamentos/s | Ganho |
|---|---|---|---|
| 1 | 80,0 ± 2,8 | 0,5 | — |
| 2 | 39,0 ± 0,7 | 1,0 | 2,05 vezes |
| 4 | 21,4 ± 0,6 | 1,9 | 3,74 vezes |

**Interpretação:** aqui o ganho é quase linear. Cada pagamento passa de 1 a 3 s esperando o "gateway" simulado (média de 2 s), então o limite é o número de consumidores, e não a CPU: 40 pagamentos × 2 s ≈ 80 s com 1 consumidor, metade com 2, um quarto com 4. Como a fila entrega cada mensagem a um único consumidor e a atualização é idempotente, basta subir mais contêineres, sem mudar o código. Com 4 o ganho fica um pouco abaixo de 4 vezes porque os últimos pagamentos terminam em momentos diferentes. É o contraponto ao Experimento 2: quando o trabalho é espera (E/S), escalar na mesma máquina funciona bem; quando é CPU, a máquina única limita. Ressalva: o atraso é simulado; um gateway real teria seus próprios limites.

## 8. Experimento de falha — queda do pagamento-service
Executar `bash scripts/falha_pagamento.sh` e `bash scripts/falha_pagamento.sh durante`.
**Comportamento observado** (logs em `evidencias/falha/`):
- *Parada com fila cheia:* com o pagamento parado, a API continuou aceitando: 20 reservas `201 PENDENTE`; `rabbitmqctl` mostrou `messages_ready=20, unacked=0`. Ao subir o pagamento, a fila foi a 0 em ~40 s e as 20 reservas viraram `CONFIRMADA`. Nenhuma perdida.
- *Queda durante o processamento:* a mensagem em curso aparecia como `unacked=1`; após matar o contêiner voltou para `ready` e foi reprocessada com a marca `(REENTREGA)` no log. Resultado final: 18 `CONFIRMADA` + 2 `RECUSADA`, nenhuma perdida.

Prints do painel do RabbitMQ para o cenário "pagamento parado" (em `evidencias/falha/`, descritos em `PRINTS_RABBITMQ.md`): fila vazia; 20 mensagens prontas com 0 consumidores; drenagem com 1 `unacked`; fila zerada, com o gráfico descendo de 20 para 0. Nessa execução as 20 reservas terminaram em 19 `CONFIRMADA` e 1 `RECUSADA`, nenhuma perdida.

### 8.1 Reinício da stack inteira com a fila cheia
Executar `bash scripts/falha_reinicio.sh`. O script para o pagamento, cria 20 reservas, faz `docker compose down` e `up`, e confere se as reservas são processadas.

- *Antes da correção (RabbitMQ sem volume):* as 20 mensagens **se perderam** no reinício. As 20 reservas ficaram `PENDENTE` para sempre e os assentos ficaram presos (`reinicio_sem_volume_*.log`). A fila era durável e as mensagens persistentes, mas os dados do RabbitMQ ficavam dentro do contêiner, que o `down` apaga.
- *Depois da correção (volume `rabbitmq_data` + hostname fixo):* nenhuma perda; as 20 reservas terminaram em 18 `CONFIRMADA` e 2 `RECUSADA` (`reinicio_com_volume_*.log`). Observação: nesse log, a leitura da fila feita logo após o RabbitMQ subir (passo 7) não retornou a fila; a prova de que as mensagens sobreviveram é o processamento das 20 reservas no passo 9.

### 8.2 Queda de uma réplica da API com carga contínua (cenário extra)
Executar `python scripts/falha_api.py --modo stop` e `--modo kill`, com 2 réplicas no ar. O script envia 25 reservas por segundo durante 36 s (900 no total), derruba uma réplica aos 12 s e a religa aos 24 s. Uma execução de cada modo (`falha_api_*.json`).

| Como a réplica caiu | Erros | Requisições lentas (> 500 ms) | Maior latência | Latência normal (p95) | Volta a atender após religar |
|---|---|---|---|---|---|
| `docker stop` (encerra com aviso) | 0 de 900 | 6, nos ~5 s após a queda | 2,09 s | ~60 ms | 6,2 s |
| `docker kill` (morte abrupta) | 1 de 900 (um `502`) | 6, nos ~5 s após a queda | 2,07 s | ~58 ms | 5,9 s |

**Interpretação:** o sistema continuou atendendo pela outra réplica, que recebeu toda a carga enquanto a primeira estava fora. Nos ~5 s seguintes à queda, algumas requisições demoraram ~2 s: o nginx ainda tinha o endereço da réplica morta (ele re-resolve o DNS a cada 5 s), esperou o tempo limite de conexão de 2 s (`proxy_connect_timeout`) e então tentou a outra réplica (`proxy_next_upstream`). Com `docker stop` a API termina as requisições em andamento antes de sair, e não houve erro. Com `docker kill` a requisição que estava em andamento na réplica morta recebeu `502`; mesmo assim essa reserva foi gravada (900 reservas no banco para 899 respostas `201`), ou seja, o cliente viu erro numa reserva que existe. Em nenhum caso houve overbooking. Depois de religada, a réplica voltou a receber tráfego em ~6 s.

## 9. Evidências

Tudo fica na pasta `evidencias/`, versionada junto com o código.

| Pasta | Conteúdo |
|---|---|
| `evidencias/exp1/` | JSON de cada rodada do Experimento 1 (3 modos × 6 repetições) e o estado da stack coletado (`compose ps`, logs da API, do pagamento e do proxy, `rabbitmqctl`, overbooking). Em `v1/`, a primeira bateria (substituída). |
| `evidencias/exp2/` | JSON de cada rodada do Experimento 2 (1, 2 e 3 réplicas × 6 repetições × 2 cargas: arquivos `leve` e `forte`), com CPU por serviço e tempo por etapa, e o estado coletado. Em `v1/`, a primeira bateria (substituída). |
| `evidencias/falha/` | Logs completos das duas variantes do experimento de falha (`falha_normal_*.log` e `falha_durante_*.log`, com a marca `REENTREGA`), o estado coletado e 4 prints do painel do RabbitMQ. Também: reinício da stack com a fila cheia, antes e depois da correção (`reinicio_*.log`), e queda de uma réplica da API (`falha_api_*.json`). |
| `evidencias/escala_pagamento/` | JSON de cada rodada do experimento extra de escala do pagamento (1, 2 e 4 consumidores × 3 repetições). |

As tabelas deste relatório são geradas por `python scripts/resumir.py exp1`, `exp2` e `pagamento` a partir desses JSON (média ± desvio padrão). O CI do GitHub Actions roda um teste de fumaça (sobe a stack e confirma que 20 pedidos ao mesmo assento resultam em exatamente 1 reserva).

## 10. Limitações e melhorias
- Redis é ponto único de falha; RabbitMQ e Postgres sem réplica. Com o Redis fora, o modo `redis` devolve `503`.
- Lock com TTL pode expirar antes do fim da operação (o índice único do Postgres continua protegendo).
- O pagamento apaga o lock com `DEL` simples, sem conferir o token (o contrato da mensagem só leva `reserva_id` e `assento_id`).
- Pagamento é simulado.
- Uma reserva `PENDENTE` nunca expira. Se a API morrer entre gravar no banco e publicar na fila, o assento fica preso até alguém intervir. O mesmo vale se a fila for apagada (`docker compose down -v`). Melhoria: um job que expira reservas `PENDENTE` antigas, ou o padrão *outbox*.
- Não há fila de erros (DLQ): uma mensagem que sempre falha no pagamento volta para a fila indefinidamente.
- Numa queda abrupta de uma réplica, a requisição em andamento recebe `502` mesmo que a reserva tenha sido gravada (visto 1 vez em 900, seção 8.2). O cliente precisaria consultar antes de tentar de novo.
- `/admin/reset` e `/admin/stats` ficam abertos por padrão (`ENABLE_ADMIN=true`) e o RabbitMQ usa `guest/guest`: aceitável em desenvolvimento, não em produção.
- Testes em uma única máquina (Windows com Docker Desktop/WSL2): gerador de carga e sistema disputam a mesma CPU, então o resultado de escalabilidade não é generalizável.
- O gerador de carga em Python é lento: com 1 processo ele limita o teste em ~100 req/s. Por isso a carga forte usa 8 processos.
- A latência vista pelo cliente no Experimento 1 mede principalmente o gerador de carga; para comparar os modos usamos o tempo medido dentro da API.
- `docker kill` conta como parada manual e não reinicia o contêiner; a política `unless-stopped` só reinicia em queda real (verificado).
- Melhorias: Redis/Postgres replicados, Redlock, Kubernetes, tracing distribuído, Prometheus/Grafana, e repetir o Experimento 2 com o gerador de carga em outra máquina, para separar o custo do gerador do custo do sistema.

## 11. Conclusão

O projeto mostra, com dados, que a verificação "está livre? então reserva" não é segura quando há várias réplicas: sem controle, o Experimento 1 aceitou em média 9,7 reservas para o mesmo assento (de 4 a 17), com overbooking nas 6 rodadas. O índice único do Postgres e o lock no Redis resolveram o problema (exatamente 1 reserva aceita em todas as rodadas). Os dois recusam um pedido em 1 a 2 ms; a diferença é que o Redis barra os perdedores antes de chegar ao banco. Manter os dois juntos dá defesa em camadas: o lock reduz a carga e o índice garante a consistência mesmo se o lock expirar.

O pagamento assíncrono por fila durável com ack manual e atualização idempotente funcionou como esperado: com o serviço parado, a API continuou aceitando reservas, a fila acumulou as mensagens e, ao religar, todas foram processadas, sem perda e com reentrega quando o consumidor foi morto no meio do trabalho. Os testes extras de falha encontraram um defeito real (as mensagens da fila se perdiam num reinício da stack, corrigido com um volume para o RabbitMQ) e mostraram que a queda de uma réplica da API não derruba o serviço: a outra réplica assume, com alguns segundos de respostas lentas.

O Experimento 2 mostrou duas coisas. Primeiro, que é preciso conferir se o teste está medindo o sistema: com a carga do enunciado, o limite era o gerador de carga, e a vazão ficou em ~100 req/s com qualquer número de réplicas. Segundo, que com carga suficiente a API sem estado escala: a vazão subiu 35% com 2 réplicas e 60% com 3, com cada réplica usando quase um núcleo inteiro. O ganho não é linear, provavelmente porque tudo roda na mesma máquina; confirmar isso exige gerar a carga a partir de outra máquina. Já o pagamento-service, cujo trabalho é espera e não CPU, escalou quase linearmente: 2,05 vezes com 2 consumidores e 3,74 vezes com 4.
