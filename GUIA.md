# Guia de uso completo

Como ligar, usar, observar, testar e desligar o sistema de reserva de ingressos.
Para entender **como o projeto foi construído e como o código funciona**, leia [`docs/EXPLICACAO.md`](docs/EXPLICACAO.md). Para o porquê das decisões, veja [`PROJETO.md`](PROJETO.md) e [`docs/decisoes.md`](docs/decisoes.md).

---

## 1. O que você está rodando

Sete contêineres Docker, todos locais e gratuitos:

| Contêiner | Para que serve | Onde acessar |
|---|---|---|
| `proxy` (nginx) | Única porta de entrada; distribui as requisições entre as réplicas da API | http://localhost:8080 |
| `api` ×2 | Recebe as reservas (FastAPI). Duas cópias idênticas | só via proxy |
| `pagamento` | Consome a fila e "paga" a reserva (1–3 s, ~10% de recusa) | só logs |
| `db` (Postgres) | Guarda eventos, assentos e reservas (volume `pgdata`) | só via `docker compose exec` |
| `redis` | Guarda os locks temporários por assento | só via `docker compose exec` |
| `rabbitmq` | Fila entre a API e o pagamento | http://localhost:15672 (`guest` / `guest`) |

Caminho de uma reserva: `cliente → nginx → api → (lock no Redis) → grava PENDENTE no Postgres → publica na fila → pagamento → CONFIRMADA ou RECUSADA`.

**Não existe tela do sistema.** Você usa a API (pelo navegador em `/docs` ou pelo terminal) e observa o resto pelos painéis e logs.

---

## 2. Antes de tudo: o Docker Desktop precisa estar aberto

1. Abra o **Docker Desktop** pelo menu Iniciar.
2. Espere o canto inferior esquerdo ficar verde (**Engine running**). Na primeira vez leva 1–2 minutos.
3. Confira no terminal:
   ```bash
   docker info --format '{{.ServerVersion}}'
   ```
   Se aparecer um número de versão, está pronto. Se der erro de conexão, o Docker Desktop ainda não subiu.

> Use o terminal na **pasta do projeto** (`D:\ReservaDeIngressos`). Todos os comandos `docker compose` dependem disso.
> Se `docker` não for reconhecido no Git Bash, feche e abra o terminal de novo, ou use o PowerShell.

---

## 3. Ligar, parar e desligar

| Quero... | Comando | O que acontece com os dados |
|---|---|---|
| **Ligar tudo** (primeira vez ou depois de alterar código) | `docker compose up -d --build` | mantidos |
| **Ligar de novo** (contêineres já existem) | `docker compose start` ou `docker compose up -d` | mantidos |
| **Pausar** (libera CPU/RAM, mantém tudo) | `docker compose stop` | mantidos |
| **Desligar e remover contêineres** | `docker compose down` | mantidos (volume fica) |
| **Zerar tudo, inclusive o banco** | `docker compose down -v` | **apagados** |
| Ver o que está rodando | `docker compose ps` | — |

Depois de ligar, espere a API responder:
```bash
bash scripts/aguardar_api.sh
curl http://localhost:8080/health
```
Resposta esperada: `{"status":"ok","instancia":"<id do contêiner>"}`.

A stack leva ~30 s para ficar pronta, porque o RabbitMQ demora a subir e os outros serviços esperam pelos healthchecks.

---

## 4. Uso de memória (o "Memory usage" que você viu)

No Docker Desktop, a aba **Containers** mostra CPU e memória de cada contêiner. Valores típicos medidos aqui:

| Contêiner | Memória aproximada |
|---|---|
| `rabbitmq` | ~140 MB |
| `db` | ~100 MB |
| `api` (cada réplica) | ~40 MB |
| `pagamento` | ~25 MB |
| `proxy` | ~14 MB |
| `redis` | ~5 MB |

Total da stack: **~400 MB**. É pouco, e a CPU fica perto de 0% quando não há carga.

O que realmente pesa é o próprio **Docker Desktop**: ele roda uma máquina virtual (WSL2) que reserva RAM mesmo com os contêineres parados. Para liberar essa memória:
1. `docker compose stop` (ou `down`)
2. Feche o Docker Desktop (menu da bandeja do Windows → **Quit Docker Desktop**).
3. Se ainda sobrar processo `vmmem` no Gerenciador de Tarefas: no PowerShell, `wsl --shutdown`.

Ver o consumo pelo terminal:
```bash
docker stats --no-stream
```

---

## 5. Usando a API

### 5.1 Pelo navegador (mais fácil): http://localhost:8080/docs

Página interativa gerada automaticamente. Clique em um endpoint → **Try it out** → preencha → **Execute**. A resposta e o código HTTP aparecem logo abaixo.

### 5.2 Pelo terminal

> **PowerShell:** `curl` é um apelido para outro comando. Use `curl.exe`, e para o corpo JSON prefira o Git Bash ou o `/docs`, porque as aspas são diferentes.

| Endpoint | Para que serve |
|---|---|
| `GET /health` | Confirma que a API está viva e mostra **qual réplica** respondeu (campo `instancia`) |
| `GET /eventos/1/assentos` | Lista os 1000 assentos com `livre` ou `reservado` |
| `POST /reservas` | Cria uma reserva |
| `GET /reservas/{id}` | Consulta o status da reserva |
| `POST /admin/reset` | Apaga todas as reservas, locks e mensagens da fila |
| `GET /admin/stats` | Contagem por status, modo ativo e lista de overbooking |

**Criar uma reserva**
```bash
curl -X POST http://localhost:8080/reservas \
  -H "Content-Type: application/json" \
  -d '{"assento_id": 10, "cliente": "elias"}'
```
Resposta `201`:
```json
{"id":1,"assento_id":10,"cliente":"elias","status":"PENDENTE","instancia":"cb6ff473f100"}
```

**Acompanhar o pagamento** (repita o comando em 1–3 s e veja o status mudar):
```bash
curl http://localhost:8080/reservas/1
```
`PENDENTE` → `CONFIRMADA` (≈90%) ou `RECUSADA` (≈10%).

**Códigos de resposta de `POST /reservas`**

| Código | Significado |
|---|---|
| `201` | Reserva criada (ainda `PENDENTE`) |
| `409` | Assento já reservado por outra pessoa |
| `404` | Assento inexistente (ids válidos: 1 a 1000) |
| `422` | Corpo inválido (falta `assento_id` ou `cliente`) |
| `503` | Alguma dependência caiu (Redis, RabbitMQ ou Postgres) |

**Testes rápidos que valem a pena fazer**
```bash
# Balanceamento: o campo "instancia" alterna entre duas réplicas
for i in 1 2 3 4 5 6; do curl -s http://localhost:8080/health; echo; done

# Assento ocupado: rode o POST acima duas vezes e veja o segundo dar 409
# Assento livre/ocupado:
curl -s http://localhost:8080/eventos/1/assentos | head -c 300

# Começar do zero
curl -X POST http://localhost:8080/admin/reset
```

> Se uma reserva for `RECUSADA`, o assento volta a ficar livre e pode ser reservado de novo.

---

## 6. Olhando por dentro

### 6.1 Logs ao vivo
```bash
docker compose logs -f api pagamento      # Ctrl+C para sair
docker compose logs --tail=50 proxy       # mostra qual réplica atendeu (upstream=...)
```
Linhas importantes: `reserva criada`, `lock negado`, `pagamento confirmada`, `processando ... (REENTREGA)`.

### 6.2 Painel do RabbitMQ: http://localhost:15672 (`guest` / `guest`)
Aba **Queues and Streams** → fila `reservas.pagamento`:
- **Ready**: mensagens esperando um consumidor.
- **Unacked**: mensagens que o pagamento pegou e ainda não confirmou.
- O gráfico mostra a fila enchendo e esvaziando.

Pelo terminal:
```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
```

### 6.3 Banco de dados (Postgres)
```bash
docker compose exec db psql -U app -d ingressos
```
Dentro do `psql`:
```sql
SELECT id, assento_id, cliente, status FROM reservas ORDER BY id;
SELECT status, count(*) FROM reservas GROUP BY status;
-- detector de overbooking (deve retornar 0 linhas nos modos db e redis):
SELECT assento_id, count(*) FROM reservas
WHERE status IN ('PENDENTE','CONFIRMADA') GROUP BY assento_id HAVING count(*) > 1;
-- índice único (existe nos modos db/redis, some no modo none):
\d reservas
\q
```
Consulta de uma linha, sem entrar no `psql`:
```bash
docker compose exec db psql -U app -d ingressos -c "SELECT count(*) FROM reservas;"
```

### 6.4 Redis (locks)
```bash
docker compose exec redis redis-cli keys 'lock:*'      # locks ativos
docker compose exec redis redis-cli ttl lock:assento:10  # segundos até expirar (30 no máximo)
```
Um lock existe por até 30 s depois de uma reserva; se o pagamento recusar, ele é apagado antes.

---

## 7. Os três modos de controle de concorrência

O modo é definido pela variável `MODO_LOCK` e vale para as réplicas da API:

| Modo | O que faz | Resultado quando 100 pedidos disputam 1 assento |
|---|---|---|
| `none` | Verifica "está livre?", espera 50 ms, grava. Sem proteção | **Várias reservas aceitas: overbooking** |
| `db` | Grava direto; o índice único do Postgres rejeita a duplicata | Só 1 aceita; as outras barradas **no banco** |
| `redis` | Pega um lock no Redis antes; quem não consegue leva 409 sem tocar no banco | Só 1 aceita; as outras barradas **no Redis** |

**Trocar de modo:**
```bash
MODO_LOCK=none docker compose up -d --force-recreate api
bash scripts/aguardar_api.sh
curl http://localhost:8080/admin/stats        # o campo "modo" confirma
```
(No PowerShell: `$env:MODO_LOCK="none"; docker compose up -d --force-recreate api`.)
Para voltar ao padrão, use `redis` ou apague a variável e recrie a API.

---

## 8. Escalar a API (mais réplicas)

```bash
docker compose up -d --scale api=3
sleep 7                                       # o nginx re-resolve o DNS a cada 5 s
for i in 1 2 3 4 5 6; do curl -s http://localhost:8080/health; echo; done   # 3 hostnames
docker compose up -d --scale api=2            # volta ao padrão
```

---

## 9. Rodando os experimentos

**Uma vez só, prepare o Python dos scripts** (a pasta `.venv` já existe nesta máquina):
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r scripts/requirements.txt     # Git Bash / Windows
```
Nos comandos abaixo, `PYTHON=.venv/Scripts/python` diz aos scripts `.sh` qual Python usar. A stack precisa estar ligada.

| Experimento | Comando | Duração | Resultado |
|---|---|---|---|
| **1. Concorrência** (3 modos × 6 rodadas) | `PYTHON=.venv/Scripts/python bash scripts/rodar_exp1.sh` | ~5 min | Tabela no final; JSON em `evidencias/exp1/` |
| **2. Escalabilidade** (1, 2 e 3 réplicas × 6, cargas leve e forte) | `PYTHON=.venv/Scripts/python bash scripts/rodar_exp2.sh` | ~11 min | Tabela no final; JSON em `evidencias/exp2/` |
| **Falha: pagamento parado** | `bash scripts/falha_pagamento.sh` | ~1 min | Log em `evidencias/falha/` |
| **Falha: pagamento morto no meio** | `bash scripts/falha_pagamento.sh durante` | ~1 min | Log com `(REENTREGA)` |
| Recarregar a tabela sem rodar de novo | `.venv/Scripts/python scripts/resumir.py exp1` (ou `exp2`) | instantâneo | Tabela em Markdown |
| Guardar logs e estado da stack | `bash scripts/coletar_evidencias.sh exp1` | segundos | Arquivos em `evidencias/exp1/` |

Os experimentos **apagam as reservas** (usam `/admin/reset`). O `rodar_exp1.sh` deixa a stack no modo `redis` no final.

**Um disparo avulso do Experimento 1** (modo atual da API):
```bash
.venv/Scripts/python scripts/carga_concorrencia.py --rep 1 --reqs 100 --assento 10
```
Opções: `--reqs` (nº de requisições), `--assento` (qual assento), `--rep` (nº da repetição, vai no nome do arquivo), `--warmup` (reservas de aquecimento, descartadas), `--out` (pasta de saída).

**Um disparo avulso do Experimento 2:**
```bash
.venv/Scripts/python scripts/carga_escala.py --replicas 2 --rep 1 --reqs 500 --conc 50                      # carga leve
.venv/Scripts/python scripts/carga_escala.py --replicas 2 --rep 1 --reqs 1000 --conc 96 --processos 8       # carga forte
```
`--replicas` precisa ser o número de réplicas que está no ar: o script confere no aquecimento e para se não bater. `--processos` divide a carga entre vários processos; com 1 só, o próprio script vira o gargalo (~100 req/s). O JSON traz também `cpu_durante_carga_pct` (CPU de cada serviço durante a carga, 100 = um núcleo) e `etapas_api_ms` (tempo de cada etapa dentro da API). Para a CPU ser medida, o comando `docker` precisa estar no `PATH` do terminal.

### Resultados que já temos

**Experimento 1** (100 pedidos simultâneos, mesmo assento, 6 rodadas por modo):

| Modo | Aceitas | Overbooking | Tempo na API para recusar |
|---|---|---|---|
| none | 9,7 (de 4 a 17) | sim (6/6) | 1,9 ms |
| db | 1 | não | 1,7 ms |
| redis | 1 | não | 1,0 ms |

**Experimento 2** (6 rodadas por configuração):

| Carga | 1 réplica | 2 réplicas | 3 réplicas |
|---|---|---|---|
| Leve (1 processo gerando carga) | 102 req/s | 99 req/s | 100 req/s |
| Forte (8 processos gerando carga) | 259 req/s | 351 req/s (+35%) | 415 req/s (+60%) |

Na carga leve o gargalo é o gerador de carga, e não a API. Na forte, cada réplica usa quase um núcleo inteiro e mais réplicas aumentam a vazão. A discussão está em [`docs/relatorio.md`](docs/relatorio.md).

---

## 10. Roteiro de demonstração (apresentação)

1. **Subir:** `docker compose up -d` e mostrar `docker compose ps` com tudo `healthy`.
2. **Balanceamento:** rodar o `curl /health` 6 vezes e apontar os dois hostnames.
3. **Reserva e pagamento assíncrono:** criar uma reserva no `/docs`, consultar e ver `PENDENTE` virar `CONFIRMADA`. Mostrar o log do `pagamento` ao lado.
4. **Overbooking:**
   ```bash
   MODO_LOCK=none docker compose up -d --force-recreate api && bash scripts/aguardar_api.sh
   .venv/Scripts/python scripts/carga_concorrencia.py --rep 1      # aceitas_201 > 1
   ```
   Depois repetir com `MODO_LOCK=redis`: `aceitas_201 = 1`.
5. **Falha:** com o painel do RabbitMQ aberto, rodar `bash scripts/falha_pagamento.sh durante` e mostrar a fila enchendo, a mensagem `unacked` voltando e a marca `(REENTREGA)`.
6. **Limitações:** Redis, RabbitMQ e Postgres sem réplica; pagamento simulado; testes em uma só máquina.

---

## 11. Configurações (arquivo `.env`)

| Variável | Padrão | Efeito |
|---|---|---|
| `MODO_LOCK` | `redis` | `none`, `db` ou `redis` (seção 7) |
| `CHECK_INSERT_DELAY_MS` | `50` | Pausa entre verificar e gravar no modo `none` (amplia a corrida) |
| `LOCK_TTL_SECONDS` | `30` | Duração máxima do lock no Redis |
| `PAGAMENTO_DELAY_MIN` / `MAX` | `1` / `3` | Tempo simulado de pagamento, em segundos |
| `PAGAMENTO_TAXA_RECUSA` | `0.1` | Chance de recusa (0.1 = 10%) |
| `ENABLE_ADMIN` | `true` | Habilita `/admin/reset` e `/admin/stats` |

Depois de editar o `.env`, recrie o serviço afetado: `docker compose up -d --force-recreate api pagamento`.
Se mudar o **código** (`api/` ou `pagamento/`), use `docker compose up -d --build`.

---

## 12. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `failed to connect to the docker API` | Docker Desktop fechado ou ainda subindo | Abrir o Docker Desktop e esperar "Engine running" |
| `docker` não é reconhecido | Terminal aberto antes da instalação | Fechar e abrir o terminal |
| `curl` falha na porta 8080 | Stack parada ou ainda subindo | `docker compose up -d` e `bash scripts/aguardar_api.sh` |
| Erro `bind: port is already allocated` | Outro programa usa 8080 ou 15672 | Fechar o programa, ou mudar a porta à esquerda em `docker-compose.yml` |
| `/health` mostra uma réplica só | nginx ainda não viu a nova réplica | Esperar ~7 s e repetir |
| `409` em tudo | Assento já reservado de rodadas anteriores | `curl -X POST http://localhost:8080/admin/reset` |
| `503` ao reservar | Redis, RabbitMQ ou Postgres fora | `docker compose ps`; subir o que estiver parado |
| Fila não esvazia | `pagamento` parado | `docker compose start pagamento` e `docker compose logs -f pagamento` |
| `docker kill` e o contêiner não volta | Comportamento do Docker: `kill` conta como parada manual | `docker compose start <serviço>`. Quedas reais reiniciam sozinhas |
| API não sobe após trocar para `db`/`redis` | Sobraram duplicatas do modo `none` impedindo o índice único | `curl -X POST http://localhost:8080/admin/reset` (recria o índice) |
| Cálculo de latência estranho no 1º disparo | Conexões frias | Rodar de novo; os scripts já aquecem a conexão |
| Quero tudo limpo | — | `docker compose down -v && docker compose up -d --build` |

---

## 13. Mapa de arquivos

| Pasta / arquivo | Conteúdo |
|---|---|
| `docker-compose.yml` | Define os 7 serviços, redes, volume e healthchecks |
| `api/app/main.py` | Rotas e a lógica dos três modos |
| `api/app/locks.py`, `db.py`, `fila.py` | Redis, Postgres e RabbitMQ da API |
| `pagamento/app/main.py` | Consumidor da fila |
| `proxy/nginx.conf` | Balanceador (re-resolve o DNS a cada 5 s) |
| `db/init.sql` | Tabelas e os 1000 assentos |
| `scripts/` | Cargas, experimentos e coleta de evidências |
| `evidencias/` | Saídas dos experimentos (JSON, logs) |
| `docs/decisoes.md`, `docs/relatorio.md` | Decisões e rascunho do relatório |
| `README.md` | Passo a passo curto de reprodução |
