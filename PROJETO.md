# Projeto: Sistema de Reserva de Ingressos com Controle de Concorrência

Trabalho semestral de **Computação Distribuída** (UFMS) — grupo: Alessandro, Anna Júlia e Elias.

> **Como usar este arquivo:** coloque-o na raiz do repositório (pode copiar como `CLAUDE.md` para o Claude Code lê-lo automaticamente). Ele é a fonte única de verdade do projeto: contexto, arquitetura, contratos, fases e critérios de pronto. Trabalhe **uma fase por vez** (seção 12) e só avance quando o teste de aceitação da fase passar.

---

## 0. Regras de trabalho com o Claude Code

1. O grupo precisa **entender e saber explicar** cada parte na apresentação. Ao implementar algo, explique o que foi feito e por quê, em linguagem de estudante de graduação (sem jargão desnecessário).
2. Trabalhe em **passos pequenos**: um componente por vez, com comando de teste ao final.
3. Faça commits pequenos e frequentes, com mensagens em português no formato `tipo: descrição` (ex.: `feat: cria endpoint POST /reservas`).
4. Não adicione tecnologias fora das listadas sem avisar e justificar (o relatório exige justificar cada escolha).
5. Tudo deve rodar **localmente, de graça**, com `docker compose up`. Nada de nuvem paga.
6. Toda decisão relevante vai para `docs/decisoes.md` (uma linha: decisão + motivo). Isso vira material do relatório.
7. Guarde as evidências (logs, saídas de comandos, prints) em `evidencias/` assim que os experimentos rodarem.

---

## 1. Contexto e requisitos da professora

Fonte: PDF "Trabalho Semestral – Computação Distribuída (Projeto Distribuído simplificado)".

**Objetivo:** desenvolver uma aplicação distribuída **pequena**, com experimentos simples e evidências. Não é um sistema comercial.

### Requisitos mínimos → como o projeto atende

| Requisito do PDF | Como atendemos |
|---|---|
| Pelo menos 3 componentes, 2 serviços/processos independentes | `reservas-api`, `pagamento-service`, Redis, RabbitMQ, Postgres, nginx |
| Comunicação entre processos | REST (cliente → API) e fila RabbitMQ (API → pagamento) |
| Contêineres (Docker/Compose) | Todos os componentes em `docker-compose.yml` |
| Persistência | Postgres com volume `pgdata`; Redis só para locks temporários |
| Processamento distribuído (concorrente/assíncrono/eventos) | Reservas concorrentes em 2+ réplicas da API; pagamento assíncrono via fila |
| 2 experimentos (1+ quantitativo) | Exp. 1 concorrência (quantitativo) e Exp. 2 escalabilidade (quantitativo) |
| 1 experimento de falha | Derrubar `pagamento-service` com carga ativa (+ cenário extra: derrubar réplica da API) |
| Evidências | Logs, comandos, métricas e prints em `evidencias/` |

### Etapas (marcos)
1. Definição do projeto (problema, proposta, arquitetura inicial)
2. Desenvolvimento (componentes + execução em Docker)
3. Experimentos (evidências e análise)
4. Entrega final (relatório, repositório, apresentação de até 30 min; **todos** participam)

### Critérios de avaliação
Projeto e arquitetura (25%) · Aplicação dos conceitos (20%) · Execução, experimentos e resultados (20%) · Documentação, evidências e reprodutibilidade (15%) · Apresentação e domínio técnico (20%).

### Bônus (até +1,0)
Serverless · Kubernetes local (kind/k3d/minikube) · Helm/Kustomize · CI/CD (GitHub Actions) · Prometheus/Grafana ou tracing · experimento extra de escalabilidade/tolerância a falhas com análise quantitativa.
**Meta do grupo:** Prometheus/Grafana (ou ao menos métricas da fila) + GitHub Actions, se sobrar tempo.

---

## 2. O problema

Quando muitas pessoas tentam reservar **o mesmo assento ao mesmo tempo**, o sistema não pode vender o mesmo lugar duas vezes (**overbooking**). Em um sistema distribuído, com várias réplicas da API atendendo em paralelo, uma verificação simples do tipo "se está livre, reserva" **não basta**: duas réplicas podem verificar ao mesmo tempo e as duas concluírem que o assento está livre (condição de corrida).

**Objetivo da aplicação:** reservar assentos de um evento garantindo consistência sob concorrência, com pagamento processado de forma assíncrona, e demonstrar (com experimentos) como o lock distribuído e as restrições do banco evitam o overbooking.

---

## 3. Arquitetura

```
                       ┌────────────────────────────┐
 Cliente / scripts ──► │  nginx (proxy, porta 8080) │
                       └─────────────┬──────────────┘
                                     │ round-robin
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
              reservas-api (réplica 1)        reservas-api (réplica 2)   ... (escalável)
                     │   │                           │   │
        SET NX (lock)│   │INSERT                     │   │
                     ▼   ▼                           ▼   ▼
                  Redis  Postgres  ◄─────────────────────┘
                     │
                     │ publica "reserva.criada"
                     ▼
                 RabbitMQ (fila durável: reservas.pagamento)
                     │
                     ▼
             pagamento-service (consumidor)
                     │ UPDATE status (CONFIRMADA | RECUSADA)
                     ▼
                  Postgres  (+ libera lock no Redis se RECUSADA)
```

### Componentes

| Componente | Imagem/Tecnologia | Responsabilidade |
|---|---|---|
| `proxy` | nginx | Única entrada exposta (porta 8080). Balanceia entre réplicas da API |
| `api` | Python 3.12 + FastAPI + Uvicorn | REST: listar assentos, criar reserva, consultar status. **Sem estado** (escalável) |
| `pagamento` | Python 3.12 + aio-pika | Consome a fila, simula pagamento (1–3 s, ~10% recusa) e atualiza o status |
| `redis` | redis:7 | Lock distribuído por assento (`SET ... NX EX`) |
| `rabbitmq` | rabbitmq:3-management | Fila entre API e pagamento (painel em `:15672` para prints) |
| `db` | postgres:16 | Persistência (volume `pgdata`) |

> **Stack:** Python/FastAPI é a sugestão por ser simples e ter boa documentação. Se o grupo preferir outra linguagem, registre a justificativa em `docs/decisoes.md` e adapte; a arquitetura não muda.

### Redes Docker
- `front-net`: `proxy` e `api`.
- `back-net`: `api`, `pagamento`, `redis`, `rabbitmq`, `db`.
- Só o `proxy` publica porta no host (`8080`). O painel do RabbitMQ (`15672`) pode ser publicado apenas em desenvolvimento, para tirar prints. **Atenção:** se `back-net` for `internal: true`, a porta publicada pode não funcionar; nesse caso ligue o `rabbitmq` também a uma rede normal ou remova `internal`.

### Fluxo de uma reserva
1. `POST /reservas` chega ao nginx, que repassa a uma réplica da API.
2. A API aplica o **modo de controle** configurado (`MODO_LOCK`, seção 5).
3. Grava a reserva como `PENDENTE` no Postgres.
4. Publica `{reserva_id, assento_id}` na fila `reservas.pagamento` (mensagem persistente).
5. Responde `201` com a reserva `PENDENTE`.
6. O `pagamento-service` consome, espera 1–3 s, decide aprovar ou recusar, atualiza para `CONFIRMADA` ou `RECUSADA` e só então dá **ack** na mensagem.
7. Se `RECUSADA`, o assento volta a ficar livre (e o lock no Redis é removido).

---

## 4. Banco de dados (Postgres)

`db/init.sql` (executado na primeira subida do contêiner):

```sql
CREATE TABLE eventos (
  id SERIAL PRIMARY KEY,
  nome TEXT NOT NULL
);

CREATE TABLE assentos (
  id SERIAL PRIMARY KEY,
  evento_id INT NOT NULL REFERENCES eventos(id),
  codigo TEXT NOT NULL,
  UNIQUE (evento_id, codigo)
);

CREATE TABLE reservas (
  id SERIAL PRIMARY KEY,
  assento_id INT NOT NULL REFERENCES assentos(id),
  cliente TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDENTE'
    CHECK (status IN ('PENDENTE','CONFIRMADA','RECUSADA')),
  criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed: 1 evento com 1000 assentos (A1..A1000) para os experimentos
INSERT INTO eventos (nome) VALUES ('Show de Teste');
INSERT INTO assentos (evento_id, codigo)
SELECT 1, 'A' || g FROM generate_series(1, 1000) AS g;
```

**Índice de unicidade (depende do modo):** "um assento só pode ter **uma** reserva ativa (PENDENTE ou CONFIRMADA)":

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_ativa
  ON reservas (assento_id)
  WHERE status IN ('PENDENTE','CONFIRMADA');
```

- A API, ao iniciar, **cria** esse índice nos modos `db` e `redis`, e **remove** (`DROP INDEX IF EXISTS`) no modo `none`. Envolva em `try/except`, pois duas réplicas podem executar ao mesmo tempo.
- Persistência: volume nomeado `pgdata` montado em `/var/lib/postgresql/data`.

**Consulta para detectar overbooking** (usar nos experimentos):

```sql
SELECT assento_id, count(*) AS reservas_ativas
FROM reservas
WHERE status IN ('PENDENTE','CONFIRMADA')
GROUP BY assento_id
HAVING count(*) > 1;
```

---

## 5. Modos de controle de concorrência (`MODO_LOCK`)

Variável de ambiente da API. É o coração do Experimento 1.

| Modo | O que faz | Resultado esperado sob disputa |
|---|---|---|
| `none` | Verifica se o assento está livre (`SELECT`), espera `CHECK_INSERT_DELAY_MS` (ex.: 50 ms) e faz `INSERT`. **Sem índice único.** | **Overbooking** (várias reservas aceitas) |
| `db` | Faz `INSERT` direto; o índice único rejeita duplicatas (captura a violação → `409`) | Só 1 aceita; as outras falham **no banco** |
| `redis` | Tenta `SET lock:assento:{id} <token> NX EX 30` antes; se falhar → `409` sem tocar no banco. Depois `INSERT` (índice também ativo, como segunda barreira) | Só 1 aceita; as outras são barradas **no Redis** (menos carga no banco) |

Detalhes do modo `redis`:
- O valor do lock é um token único (UUID) por requisição.
- Em caso de erro ao gravar no banco, **libere o lock** (apague a chave) para não prender o assento.
- A liberação segura é "apagar só se o valor for meu" (script Lua `compare-and-delete`) ou, mais simples, aceitar o TTL de 30 s e documentar como limitação.

`CHECK_INSERT_DELAY_MS` existe só para **ampliar a janela da condição de corrida** de forma reprodutível no modo `none`. Documente isso no relatório.

---

## 6. Contrato da API

Base: `http://localhost:8080`

### `GET /health`
`200 {"status":"ok","instancia":"<hostname>"}` — o hostname do contêiner aparece na resposta, o que prova o balanceamento entre réplicas nos logs/prints.

### `GET /eventos/{evento_id}/assentos`
```json
[
  {"id": 1, "codigo": "A1", "status": "livre"},
  {"id": 2, "codigo": "A2", "status": "reservado"}
]
```
`status` = `reservado` se existir reserva ativa (PENDENTE/CONFIRMADA); senão `livre`.

### `POST /reservas`
Corpo:
```json
{"assento_id": 10, "cliente": "elias"}
```
Respostas:
- `201` → `{"id": 42, "assento_id": 10, "cliente": "elias", "status": "PENDENTE", "instancia": "<hostname>"}`
- `409` → `{"erro": "assento indisponível"}`
- `404` → assento inexistente
- `503` → dependência fora do ar (ex.: Redis ou RabbitMQ caído)

### `GET /reservas/{id}`
`200` com a reserva e o status atual (`PENDENTE`, `CONFIRMADA` ou `RECUSADA`); `404` se não existir.

### `POST /admin/reset` (somente desenvolvimento)
Apaga todas as reservas e limpa as chaves `lock:*` do Redis. Usado entre as rodadas dos experimentos. Proteger por variável `ENABLE_ADMIN=true`.

### `GET /admin/stats` (opcional, útil nos experimentos)
`{"reservas_por_status": {...}, "overbooking": [...]}` usando a consulta da seção 4.

---

## 7. Fila (RabbitMQ)

- Fila: `reservas.pagamento`, **durável**.
- Mensagens **persistentes** (`delivery_mode=2`), corpo JSON `{"reserva_id": 42, "assento_id": 10}`.
- Consumidor com **ack manual** e `prefetch_count=1`: só dá `ack` **depois** de atualizar o banco. Se o consumidor morrer no meio, a mensagem volta para a fila (reentrega). Isso é o que o experimento de falha demonstra.
- **Idempotência:** o `pagamento-service` só atualiza se o status atual for `PENDENTE` (`UPDATE ... WHERE id=$1 AND status='PENDENTE'`), para que uma reentrega não gere efeito duplicado.
- Reconexão com **retry/backoff** na API e no pagamento (o RabbitMQ pode demorar a subir).

---

## 8. Estrutura do repositório

```
.
├── CLAUDE.md                 # cópia deste arquivo (opcional)
├── PROJETO.md                # este arquivo
├── README.md                 # passo a passo de execução e reprodução
├── docker-compose.yml
├── .env.example              # variáveis (MODO_LOCK, delays, credenciais)
├── db/
│   └── init.sql
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # rotas FastAPI
│       ├── config.py         # variáveis de ambiente
│       ├── db.py             # conexão/pool Postgres
│       ├── locks.py          # lock Redis (modos)
│       └── fila.py           # publicação no RabbitMQ
├── pagamento/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/main.py           # consumidor da fila
├── proxy/
│   └── nginx.conf
├── scripts/
│   ├── carga_concorrencia.py # Experimento 1
│   ├── carga_escala.py       # Experimento 2
│   ├── falha_pagamento.sh    # Experimento de falha
│   └── coletar_evidencias.sh
├── evidencias/
│   ├── exp1/  exp2/  falha/
├── docs/
│   ├── decisoes.md
│   ├── relatorio.md          # rascunho do relatório técnico
│   └── diagrama.png
└── .github/workflows/ci.yml  # bônus
```

---

## 9. Docker Compose — pontos obrigatórios

- Serviços: `proxy`, `api`, `pagamento`, `redis`, `rabbitmq`, `db`.
- `restart: unless-stopped` em todos.
- **Healthchecks** (`pg_isready`, `redis-cli ping`, `rabbitmq-diagnostics -q ping`) e `depends_on` com `condition: service_healthy`.
- Volume nomeado `pgdata`; `db/init.sql` montado em `/docker-entrypoint-initdb.d/`.
- Variáveis via `.env` (nunca commitar senhas reais; usar `.env.example`).
- Redes `front-net` e `back-net` (seção 3).
- `api` **sem** `container_name` e **sem** `ports` (para poder escalar com `--scale api=N`).
- **Armadilha do nginx com réplicas:** o nginx resolve o nome `api` **uma vez, ao iniciar**. Depois de `docker compose up --scale api=3`, reinicie o proxy (`docker compose restart proxy`) **ou** use `resolver 127.0.0.11 valid=5s;` com `set $upstream http://api:8000; proxy_pass $upstream;` para re-resolver o DNS. Registre a solução em `docs/decisoes.md`.
- Logs: a API e o pagamento devem logar uma linha por evento importante (`reserva criada`, `lock negado`, `pagamento confirmado`, com hostname e id), pois viram evidência.

### Variáveis de ambiente (`.env.example`)

```
POSTGRES_USER=app
POSTGRES_PASSWORD=app
POSTGRES_DB=ingressos
DATABASE_URL=postgresql://app:app@db:5432/ingressos
REDIS_URL=redis://redis:6379/0
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
MODO_LOCK=redis                # none | db | redis
CHECK_INSERT_DELAY_MS=50       # janela da condição de corrida (modo none)
LOCK_TTL_SECONDS=30
PAGAMENTO_DELAY_MIN=1
PAGAMENTO_DELAY_MAX=3
PAGAMENTO_TAXA_RECUSA=0.1
ENABLE_ADMIN=true
```

---

## 10. Experimentos

Regra geral: cada experimento tem **objetivo, hipótese, procedimento (comandos), métricas, resultado e interpretação**. Salve a saída bruta em `evidencias/` e **repita 3 vezes**, reportando a média.

### Experimento 1 — Concorrência no mesmo assento (quantitativo)
- **Hipótese:** sem controle, haverá overbooking; com `db` ou `redis`, só 1 reserva é aceita.
- **Procedimento:** para cada `MODO_LOCK` em `none`, `db`, `redis`:
  1. Suba a stack com o modo (`MODO_LOCK=none docker compose up -d --force-recreate api`).
  2. `POST /admin/reset`.
  3. Dispare **100 requisições simultâneas** (`asyncio` + `httpx`, por exemplo) de `POST /reservas` para o **mesmo** `assento_id`.
  4. Colete: nº de respostas `201`, nº de `409`, outros erros, latência média e p95, e o resultado da consulta de overbooking.
- **Tabela de resultados (modelo):**

| Modo | 201 (aceitas) | 409 | Erros | Lat. média | Lat. p95 | Overbooking? |
|---|---|---|---|---|---|---|
| none | ? | ? | ? | ? | ? | ? |
| db | ? | ? | ? | ? | ? | ? |
| redis | ? | ? | ? | ? | ? | ? |

- **Interpretação esperada:** `none` aceita várias (violação de consistência); `db` e `redis` aceitam exatamente 1. Comparar quem barra a disputa (banco vs Redis) e o impacto na latência.

### Experimento 2 — Escalabilidade da API (quantitativo)
- **Hipótese:** mais réplicas da API aumentam a vazão (req/s) e/ou reduzem a latência sob carga.
- **Procedimento:** com `MODO_LOCK=redis`, para 1, 2 e 3 réplicas (`docker compose up -d --scale api=N` + reiniciar o proxy):
  1. `POST /admin/reset`.
  2. Envie **500 reservas em assentos diferentes**, com concorrência fixa (ex.: 50 conexões simultâneas).
  3. Colete: tempo total, **requisições/s**, latência média e p95, distribuição das respostas por `instancia` (prova do balanceamento).
- **Honestidade nos resultados:** tudo roda numa única máquina, então o ganho pode ser pequeno ou até nulo (CPU compartilhada, gerador de carga competindo). **Isso não é erro:** analise e explique no relatório (gargalo no banco, no Redis ou na própria máquina).

### Experimento de falha — queda do `pagamento-service`
- **Objetivo:** observar que as reservas não se perdem quando o consumidor cai.
- **Procedimento (`scripts/falha_pagamento.sh`):**
  1. `POST /admin/reset`.
  2. `docker compose stop pagamento`.
  3. Crie ~20 reservas em assentos diferentes → todas `201 PENDENTE` (a API continua funcionando).
  4. Verifique a fila: `docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged` → ~20 mensagens prontas.
  5. Consulte `GET /reservas/{id}` → continuam `PENDENTE`.
  6. `docker compose start pagamento` e acompanhe `docker compose logs -f pagamento`.
  7. Confirme que a fila zera e as reservas viram `CONFIRMADA`/`RECUSADA`, **sem perda**.
- **Cenário extra (opcional, vale como bônus de tolerância a falhas):** com carga contínua e 2 réplicas, derrube uma réplica da API (`docker stop <container>`) e meça erros e tempo de recuperação; o proxy deve continuar atendendo pela outra réplica.
- **Variação interessante:** parar o pagamento **durante** o processamento (enquanto uma mensagem está `unacked`) e mostrar a **reentrega** da mensagem.

### Coleta de evidências (`scripts/coletar_evidencias.sh`)
Salvar em `evidencias/<experimento>/`: saída do script de carga (JSON/CSV), `docker compose ps`, trechos de `docker compose logs`, saída do `rabbitmqctl list_queues`, resultado da consulta de overbooking e **prints** do painel do RabbitMQ (`:15672`). Nomear com data/hora.

---

## 11. Documentação e entregáveis

### README.md (reprodutibilidade — vale 15% da nota)
Deve permitir que qualquer pessoa, em uma máquina limpa, faça:
1. Pré-requisitos (Docker Desktop, Python 3.12 para os scripts).
2. `cp .env.example .env`
3. `docker compose up -d --build`
4. Teste rápido (`curl http://localhost:8080/health`).
5. Como rodar cada experimento (comandos exatos) e onde ficam as evidências.
6. Como parar e limpar (`docker compose down -v`).

### Relatório técnico (seguir a seção 4 do PDF)
Problema e objetivo · Arquitetura e componentes · Comunicação entre componentes · Como executar (Docker) · Persistência · 2 experimentos (descrição, resultados, interpretação) · Experimento de falha (comportamento observado) · Evidências · Limitações e melhorias.

**Limitações para citar (honestidade rende nota):** Redis é ponto único de falha; RabbitMQ e Postgres sem réplica; lock com TTL pode expirar antes do fim da operação; pagamento é simulado; testes em uma única máquina.
**Melhorias possíveis:** Redis/Postgres replicados, Redlock, Kubernetes, tracing distribuído.

### Apresentação (até 30 min, todos falam)
Problema → arquitetura → demonstração ao vivo (`docker compose up`, reserva concorrente, derrubar o pagamento) → resultados dos experimentos → limitações. Ensaiar a troca de quem fala.

---

## 12. Plano de implementação por fases

Cada fase termina com um **teste de aceitação**. Só avance quando passar.

### Fase 0 — Preparação
- Criar o repositório, estrutura de pastas, `.gitignore`, `.env.example`.
- Todos instalam o Docker Desktop e rodam `docker compose version`.
- ✅ **Aceite:** `docker run hello-world` funciona em todas as máquinas.

### Fase 1 — Banco de dados
- `db/init.sql`, serviço `db` no Compose com volume `pgdata` e healthcheck.
- ✅ **Aceite:** `docker compose up -d db` e `docker compose exec db psql -U app -d ingressos -c "SELECT count(*) FROM assentos;"` retorna `1000`. Após `down` (sem `-v`) e `up`, os dados continuam.

### Fase 2 — API básica (sem controle)
- FastAPI com `/health`, `GET /eventos/{id}/assentos`, `POST /reservas` (modo `none` primeiro), `GET /reservas/{id}`; Dockerfile; serviço no Compose.
- ✅ **Aceite:** `curl` cria uma reserva e `GET` a retorna; o assento passa a `reservado`.

### Fase 3 — Modos de concorrência
- Implementar `MODO_LOCK` (`none`, `db`, `redis`), índice condicional, `CHECK_INSERT_DELAY_MS`, `/admin/reset` e `/admin/stats`. Adicionar Redis ao Compose.
- ✅ **Aceite:** teste manual com 2 requisições paralelas ao mesmo assento: no modo `none` as duas passam (overbooking); em `db` e `redis`, uma recebe `409`.

### Fase 4 — Fila e publicação
- Adicionar RabbitMQ ao Compose; a API publica `reserva.criada` após gravar a reserva; retry de conexão.
- ✅ **Aceite:** após `POST /reservas`, o painel `:15672` mostra a mensagem na fila `reservas.pagamento`.

### Fase 5 — pagamento-service
- Consumidor com ack manual, `prefetch=1`, delay 1–3 s, recusa ~10%, idempotência; libera o lock se `RECUSADA`.
- ✅ **Aceite:** a reserva muda de `PENDENTE` para `CONFIRMADA`/`RECUSADA` e a fila zera.

### Fase 6 — Proxy e réplicas
- nginx com `upstream`/resolver; Compose com `deploy`/`--scale api=2`; hostname na resposta.
- ✅ **Aceite:** várias chamadas a `/health` mostram **hostnames diferentes** (balanceamento).

### Fase 7 — Robustez
- Healthchecks, `depends_on` com `service_healthy`, `restart: unless-stopped`, logs padronizados.
- ✅ **Aceite:** `docker compose down && docker compose up -d` sobe tudo sozinho e ordenado; uma queda real de um contêiner (o processo morrendo sozinho) faz ele voltar. *(Ajustado: `docker kill`/`docker stop` contam como parada manual e não disparam o `restart: unless-stopped`; ver `docs/decisoes.md`.)*

### Fase 8 — Scripts de experimento
- `carga_concorrencia.py`, `carga_escala.py`, `falha_pagamento.sh`, `coletar_evidencias.sh`.
- ✅ **Aceite:** cada script roda de ponta a ponta e salva a saída em `evidencias/`.

### Fase 9 — Rodar os experimentos
- Executar os 3 experimentos, 3 repetições cada, preencher as tabelas, tirar prints. *(Os Experimentos 1 e 2 foram refeitos depois com 6 repetições e ordem alternada; ver `docs/relatorio.md`.)*
- ✅ **Aceite:** pasta `evidencias/` completa e tabelas preenchidas no rascunho do relatório.

### Fase 10 — Relatório, README e apresentação
- Finalizar `README.md`, `docs/relatorio.md`, diagrama (`docs/diagrama.png`), slides e ensaio.
- ✅ **Aceite:** uma pessoa de fora clona o repositório e reproduz tudo seguindo só o README.

### Fase 11 — Bônus (se houver tempo)
- Prometheus + Grafana (métricas da API e tamanho da fila) · GitHub Actions (`docker compose up` + teste de fumaça) · experimento extra de tolerância a falhas com análise quantitativa.

---

## 13. Divisão sugerida (ajustar com o grupo)

| Pessoa | Foco principal | Fases |
|---|---|---|
| Alessandro | API de reservas e lógica de lock/modos | 2, 3 |
| Anna Júlia | pagamento-service, RabbitMQ e Postgres | 1, 4, 5 |
| Elias | Docker Compose, nginx, scripts de carga e evidências | 0, 6, 7, 8, 9 |
| Todos | Relatório, slides, revisão cruzada do código e ensaio | 10 |

Cada um deve **revisar e rodar** a parte dos outros antes da apresentação: todos precisam saber explicar o projeto inteiro.

---

## 14. Checklist final (Definição de pronto)

- [ ] `docker compose up -d --build` sobe a aplicação inteira do zero
- [ ] Mínimo de 3 componentes, com 2+ serviços independentes (API e pagamento)
- [ ] Comunicação por REST e por fila funcionando
- [ ] Persistência em volume, dados sobrevivem ao reinício
- [ ] Processamento assíncrono demonstrado (pagamento via fila)
- [ ] Experimento 1 (concorrência) com tabela e interpretação
- [ ] Experimento 2 (escalabilidade) com tabela e interpretação
- [ ] Experimento de falha com comportamento observado e registrado
- [ ] Evidências em `evidencias/` (logs, comandos, métricas, prints)
- [ ] README permite reproduzir tudo
- [ ] Relatório com todas as seções do item 4 do PDF
- [ ] Trello atualizado (A Fazer / Em Andamento / Concluído)
- [ ] Apresentação ensaiada, todos os integrantes falam

---

## 15. Prompts para começar com o Claude Code

Cole um por vez, na ordem:

1. **Início:** "Leia o `PROJETO.md` inteiro. Resuma em poucas linhas o que vamos construir e proponha o que faremos na Fase 0. Não escreva código ainda."
2. **Fase 1:** "Vamos para a Fase 1 do `PROJETO.md`. Crie `db/init.sql` e o serviço `db` no `docker-compose.yml` com volume e healthcheck. Explique cada parte e me dê os comandos de teste de aceite."
3. **Fase 2:** "Fase 2: crie a API FastAPI básica (modo `none`), o Dockerfile e o serviço no Compose. Explique como a API se conecta ao Postgres."
4. **Padrão para as demais fases:** "Vamos para a Fase N do `PROJETO.md`. Implemente só o que ela pede, explique como se fosse para um colega de faculdade e me dê o teste de aceite para eu rodar."
5. **Quando algo quebrar:** "Rodei `<comando>` e deu este erro: `<cole o erro>`. Explique a causa e corrija mantendo o que já funcionava."
