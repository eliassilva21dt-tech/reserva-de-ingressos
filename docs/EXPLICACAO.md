# O projeto explicado do zero, passo a passo

Este documento é para **estudar e explicar o projeto na apresentação**. Ele conta o que foi construído, em que ordem, por que cada peça existe e como o código funciona, em linguagem de graduação. Para usar o sistema, veja o [`GUIA.md`](../GUIA.md); para os resultados, o [`relatorio.md`](relatorio.md).

Sumário
1. [A ideia em um minuto](#1-a-ideia-em-um-minuto)
2. [Conceitos que você precisa dominar](#2-conceitos-que-você-precisa-dominar)
3. [Mapa do repositório](#3-mapa-do-repositório)
4. [Como o projeto foi construído, fase por fase](#4-como-o-projeto-foi-construído-fase-por-fase)
5. [Passeio pelo código](#5-passeio-pelo-código)
6. [Os três modos de controle, lado a lado](#6-os-três-modos-de-controle-lado-a-lado)
7. [O que acontece quando algo dá errado](#7-o-que-acontece-quando-algo-dá-errado)
8. [Os experimentos: o que medem e como ler](#8-os-experimentos-o-que-medem-e-como-ler)
9. [Perguntas que podem fazer na apresentação](#9-perguntas-que-podem-fazer-na-apresentação)
10. [Glossário](#10-glossário)

---

## 1. A ideia em um minuto

Imagine a venda de ingressos de um show. Mil pessoas abrem o site ao mesmo tempo e algumas querem **o mesmo assento**. O sistema precisa garantir que **só uma** consiga. Vender o mesmo lugar duas vezes se chama **overbooking**.

Em um sistema pequeno, com um único servidor, isso é fácil. Em um **sistema distribuído** existem várias cópias do servidor (réplicas) atendendo ao mesmo tempo. Cada cópia pergunta ao banco "o assento A10 está livre?", todas ouvem "sim" no mesmo instante, e todas vendem. Esse erro é uma **condição de corrida** (*race condition*).

O projeto faz duas coisas:
1. **Constrói** o sistema de reservas com três formas de lidar com a disputa: nenhuma (para mostrar o erro), o banco de dados e o Redis.
2. **Mede** o que acontece em cada forma, com experimentos e evidências.

O pagamento é um segundo serviço, separado, que recebe os pedidos por uma **fila** e processa depois. Assim, a reserva responde rápido e o pagamento (que é lento) acontece "por trás".

```
Cliente → nginx → API (2 réplicas) → grava no Postgres → põe na fila → pagamento → atualiza o Postgres
                      └── usa o Redis como cadeado por assento
```

---

## 2. Conceitos que você precisa dominar

### 2.1 Condição de corrida
Duas operações dependem de uma verificação que fica **desatualizada** antes de agirem. No projeto: "verificar se está livre" e "gravar a reserva" são dois passos separados. Entre eles, outra réplica pode entrar.

```
Réplica 1: verifica → livre!      ........ grava
Réplica 2:       verifica → livre! ........ grava      ← as duas gravaram: overbooking
```

### 2.2 Lock distribuído (cadeado)
Antes de mexer em um assento, a API tenta "pegar o cadeado" daquele assento no **Redis**. Só quem consegue pega segue em frente; os outros recebem "ocupado". Como o Redis é **um só** e atende um comando por vez, não existe duas réplicas pegando o mesmo cadeado.

O comando usado é `SET lock:assento:10 <token> NX EX 30`:
- `NX`: só cria se **não existir** (é isso que faz dele um cadeado);
- `EX 30`: expira em 30 segundos (se a API morrer, o assento não fica preso para sempre);
- `<token>`: um código aleatório da requisição, para saber de quem é o cadeado.

### 2.3 Restrição única do banco (índice único parcial)
O banco de dados é a **última defesa**. Foi criado um índice que diz: "para cada `assento_id` só pode existir **uma** reserva com status `PENDENTE` ou `CONFIRMADA`". Se alguém tenta gravar uma segunda, o Postgres **recusa** com um erro (`UniqueViolation`).

```sql
CREATE UNIQUE INDEX uq_reserva_ativa ON reservas (assento_id)
WHERE status IN ('PENDENTE','CONFIRMADA');
```
"Parcial" significa que a regra só vale para as linhas ativas. Uma reserva `RECUSADA` não conta, então o assento pode ser reservado de novo.

### 2.4 Fila de mensagens e processamento assíncrono
Em vez de a API esperar o pagamento (1 a 3 segundos), ela **deixa um recado numa fila** (RabbitMQ) e responde ao cliente na hora. Outro programa, o `pagamento-service`, lê os recados e processa. Vantagens:
- a reserva responde rápido;
- se o pagamento cair, os recados **ficam guardados** e são processados quando ele voltar;
- dá para ter vários consumidores no futuro.

Três detalhes que fazem a fila ser confiável:
- **Fila durável + mensagem persistente:** sobrevivem a um reinício do RabbitMQ.
- **Ack manual:** o consumidor só avisa "recebi e terminei" (`ack`) **depois** de gravar no banco. Se morrer antes, o RabbitMQ entrega a mensagem de novo.
- **`prefetch=1`:** o consumidor recebe uma mensagem por vez.

### 2.5 Idempotência
Uma operação é idempotente se executá-la duas vezes dá o mesmo resultado que executá-la uma. Como a mensagem pode ser entregue de novo, o pagamento só atualiza se a reserva **ainda estiver PENDENTE**:

```sql
UPDATE reservas SET status = $2 WHERE id = $1 AND status = 'PENDENTE'
```
Se já foi processada, o `UPDATE` não altera nenhuma linha, e nada acontece de duplicado.

### 2.6 Balanceamento de carga e réplicas
A API **não guarda estado** (tudo fica no banco e no Redis). Por isso dá para ligar quantas cópias quiser. O **nginx** fica na frente e reparte as requisições entre elas (*round-robin*: uma para cada, em sequência).

### 2.7 Contêineres e Docker Compose
Cada peça roda isolada em um **contêiner**. O `docker-compose.yml` descreve todas as peças, as redes entre elas e a ordem de subida. Com um comando (`docker compose up`) tudo sobe igual em qualquer máquina.

### 2.8 Healthcheck
Um teste automático que diz se um serviço está **realmente pronto** (não só iniciado). O Compose usa isso para esperar: a API só sobe depois que o banco, o Redis e o RabbitMQ estiverem saudáveis.

---

## 3. Mapa do repositório

| Caminho | O que é |
|---|---|
| `docker-compose.yml` | Descreve os 7 contêineres, redes, volume e healthchecks |
| `.env.example` / `.env` | Variáveis de configuração (o `.env` não vai para o git) |
| `db/init.sql` | Cria as tabelas e os 1000 assentos na primeira subida |
| `api/app/main.py` | As rotas REST e a lógica dos três modos |
| `api/app/config.py` | Lê as variáveis de ambiente |
| `api/app/db.py` | Conexão com o Postgres e o índice único |
| `api/app/locks.py` | O lock no Redis |
| `api/app/fila.py` | Publicação na fila RabbitMQ |
| `pagamento/app/main.py` | O consumidor da fila |
| `proxy/nginx.conf` | O balanceador |
| `scripts/` | Geradores de carga, experimentos e coleta de evidências |
| `evidencias/` | Resultados dos experimentos (JSON, logs, prints) |
| `docs/` | Relatório, decisões, diagrama, slides e este documento |
| `.github/workflows/ci.yml` | Teste automático no GitHub |

---

## 4. Como o projeto foi construído, fase por fase

O `PROJETO.md` define 12 fases, cada uma com um **teste de aceite**. Esta é a história de cada uma.

### Fase 0 — Preparação
Estrutura de pastas, `.gitignore` (para não enviar o `.env` nem o `.venv`) e `.env.example`. **Aceite:** `docker run hello-world` funciona.

### Fase 1 — Banco de dados
Criamos o `db/init.sql` com três tabelas (`eventos`, `assentos`, `reservas`) e populamos 1000 assentos. O serviço `db` usa um **volume** (`pgdata`) para que os dados sobrevivam a reinícios. **Aceite:** `SELECT count(*) FROM assentos` retorna 1000, e continua 1000 depois de `docker compose down` e `up`.

### Fase 2 — API básica
FastAPI com `/health`, listagem de assentos, `POST /reservas` e `GET /reservas/{id}`. A API se conecta ao Postgres por um **pool de conexões** (um grupo de conexões reaproveitadas, para não abrir uma nova a cada requisição). **Aceite:** criar uma reserva e vê-la voltar no `GET`, com o assento aparecendo como `reservado`.

### Fase 3 — Modos de concorrência
Implementamos `MODO_LOCK` com `none`, `db` e `redis`, mais `/admin/reset` e `/admin/stats`. **Aceite:** duas requisições paralelas ao mesmo assento: no modo `none` as duas passam (overbooking); nos outros, uma recebe `409`.

### Fase 4 — Fila e publicação
A API passou a publicar `{reserva_id, assento_id}` na fila `reservas.pagamento` depois de gravar. **Aceite:** a mensagem aparece no painel do RabbitMQ.

### Fase 5 — pagamento-service
O consumidor com ack manual, `prefetch=1`, atraso de 1 a 3 s, ~10% de recusa e idempotência. **Aceite:** a reserva muda de `PENDENTE` para `CONFIRMADA` ou `RECUSADA` e a fila zera.

### Fase 6 — Proxy e réplicas
O nginx na frente de duas réplicas da API. A resposta do `/health` traz o nome do contêiner (`instancia`), o que prova o balanceamento. **Aceite:** várias chamadas mostram nomes diferentes.

### Fase 7 — Robustez
Healthchecks, `depends_on` com `condition: service_healthy`, `restart: unless-stopped`, logs padronizados. **Aceite:** `down` e `up` sobem tudo em ordem; uma queda real de um contêiner o reinicia sozinho.

> **Descoberta importante:** `docker kill` **não** reinicia o contêiner, porque o Docker o trata como parada manual. A política só vale para queda real (o processo morrendo sozinho). Testamos com `redis-cli shutdown` e com `SIGINT` no processo do pagamento.

### Fase 8 — Scripts de experimento
`carga_concorrencia.py`, `carga_escala.py`, `falha_pagamento.sh` e `coletar_evidencias.sh`, mais os `rodar_exp1.sh` e `rodar_exp2.sh` que repetem tudo 3 vezes.

### Fase 9 — Rodar os experimentos
Executamos os três experimentos, 3 repetições cada, e guardamos tudo em `evidencias/`.

### Fase 10 — Documentação
README, guia, relatório, diagrama e slides.

### Fase 11 — Bônus
O CI no GitHub Actions foi feito e passa. Prometheus/Grafana não foi feito.

---

## 5. Passeio pelo código

### 5.1 `db/init.sql`

Cria `eventos` (1 evento de teste), `assentos` (A1 a A1000) e `reservas` (com `status` restrito a `PENDENTE`, `CONFIRMADA` e `RECUSADA` por um `CHECK`). Também há um índice comum `idx_reservas_assento`, que só serve para acelerar a pergunta "este assento tem reserva ativa?". **O índice único parcial não está aqui de propósito:** quem o cria ou remove é a API ao iniciar, conforme o modo.

### 5.2 `docker-compose.yml`

Pontos para saber explicar:
- **Sete serviços:** `db`, `redis`, `rabbitmq`, `api`, `pagamento`, `proxy` e o volume `pgdata`.
- **`api` sem `container_name` e sem `ports`:** só assim se pode ter várias réplicas (nomes e portas fixos causariam conflito). `deploy.replicas: 2` sobe duas; `--scale api=3` muda para três.
- **Duas redes:** `front-net` (proxy e API) e `back-net` (API, pagamento, Redis, RabbitMQ e banco). O banco e o Redis não ficam acessíveis pelo lado de fora.
- **Só o `proxy` publica porta (8080).** O RabbitMQ publica a 15672 apenas para o painel, em desenvolvimento.
- **Variáveis com valor padrão:** `${MODO_LOCK:-redis}`. Por isso `MODO_LOCK=none docker compose up -d api` troca o modo sem editar arquivo.

### 5.3 `proxy/nginx.conf`

O truque: o nginx normalmente resolve o nome `api` **uma vez**, ao iniciar, e nunca mais. Se você aumentasse as réplicas, ele não perceberia. A solução tem duas partes:
```
resolver 127.0.0.11 valid=5s;     # o DNS interno do Docker, re-consultado a cada 5 s
set $upstream http://api:8000;
proxy_pass $upstream;              # usar uma variável obriga o nginx a re-resolver
```
Também há `proxy_next_upstream error timeout`: se uma réplica não aceitar a conexão, tenta outra. O log de acesso mostra `upstream=...`, que é a prova de qual réplica atendeu.

### 5.4 `api/app/config.py`
Lê as variáveis de ambiente (URLs do banco, Redis e RabbitMQ, `MODO_LOCK`, atraso do modo `none`, TTL do lock) e guarda o `HOSTNAME` do contêiner. Se `MODO_LOCK` for inválido, a API nem inicia.

### 5.5 `api/app/db.py`
- `criar_pool()`: conecta ao Postgres **tentando de novo** se ele ainda estiver subindo (espera cada vez maior, até 5 s).
- `ajustar_indice()`: no modo `none` **remove** o índice único; nos modos `db` e `redis` **cria**. Está em `try/except` porque duas réplicas podem executar ao mesmo tempo, e porque, se sobraram duplicatas de um teste anterior, o `CREATE` falha (o `/admin/reset` resolve, pois chama a função de novo).
- `assento_existe`, `assento_ocupado`, `inserir_reserva`, `apagar_reserva`: consultas simples.
- `SQL_OVERBOOKING`: a consulta que detecta assentos com mais de uma reserva ativa.

### 5.6 `api/app/locks.py`
- `adquirir()`: o `SET ... NX EX` que vira o cadeado.
- `liberar()`: usa um **script Lua** que só apaga a chave **se o valor for o token de quem pede**. Isso evita um erro sutil: se o seu cadeado expirou e outra pessoa pegou o mesmo assento, você não pode apagar o cadeado dela.
- `limpar_todos()`: apaga todos os `lock:*` (usado no reset).

### 5.7 `api/app/fila.py`
- `conectar()`: usa `connect_robust`, que **reconecta sozinho** se o RabbitMQ cair, e declara a fila durável.
- `publicar()`: envia a mensagem como **persistente**, com limite de 5 s (para a requisição não ficar presa se o RabbitMQ estiver fora).
- `purgar()`: esvazia a fila (usado no reset).

### 5.8 `api/app/main.py` — o coração

**Inicialização (`lifespan`):** abre o pool do Postgres, o cliente do Redis, conecta na fila e ajusta o índice. Só então a API passa a responder.

**`POST /reservas`** segue três etapas:

1. **Controle de concorrência, conforme o modo** (seção 6).
2. **Grava a reserva `PENDENTE`.** Trata os erros do banco: violação do índice único vira `409`; assento inexistente (chave estrangeira) vira `404`.
3. **Publica na fila.** Se falhar, a API **apaga a reserva que acabou de criar** e responde `503`. Sem isso ficaria uma reserva `PENDENTE` para sempre, porque ninguém processaria o pagamento.

Se o Redis ou o Postgres estiverem fora, a resposta é `503`.

**Outras rotas:** `GET /health` (devolve o nome da instância), `GET /eventos/{id}/assentos` (cada assento é `livre` ou `reservado`), `GET /reservas/{id}`, `POST /admin/reset` (apaga tudo, limpa locks e fila, recria o índice) e `GET /admin/stats` (contagem por status e overbooking). As duas últimas só funcionam com `ENABLE_ADMIN=true`.

### 5.9 `pagamento/app/main.py`
Para cada mensagem recebida:
1. lê `reserva_id` e `assento_id` (mensagem inválida é descartada);
2. espera de 1 a 3 s (o "gateway de pagamento" simulado);
3. sorteia: ~10% `RECUSADA`, ~90% `CONFIRMADA`;
4. executa o `UPDATE ... WHERE status = 'PENDENTE'` (idempotente);
5. se foi `RECUSADA`, apaga o lock no Redis, liberando o assento;
6. **só então** dá o `ack`.

Se der erro no meio, devolve a mensagem para a fila (`nack` com `requeue`). Se o processo morrer, o RabbitMQ a entrega de novo (no log aparece `REENTREGA`).

### 5.10 `scripts/`
- `carga_concorrencia.py` (Experimento 1): faz `reset`, "aquece" 100 conexões, faz todas as 100 requisições largarem **ao mesmo tempo** (um `asyncio.Event` funciona como a "largada") e mede cada resposta e a latência. No final consulta `/admin/stats` para saber se houve overbooking e salva um JSON.
- `carga_escala.py` (Experimento 2): 500 reservas em **assentos diferentes**, com 50 conexões simultâneas, medindo req/s, latência e **qual instância respondeu** cada uma.
- `falha_pagamento.sh`: para o pagamento, cria 20 reservas, mostra a fila cheia, religa e acompanha até zerar. A variante `durante` mata o pagamento enquanto ele processa uma mensagem.
- `rodar_exp1.sh` / `rodar_exp2.sh`: repetem os experimentos nos três modos, ou com 1, 2 e 3 réplicas, 3 vezes cada.
- `resumir.py`: calcula as médias e imprime as tabelas.
- `coletar_evidencias.sh`: guarda o estado da stack (`compose ps`, logs, filas, overbooking).

---

## 6. Os três modos de controle, lado a lado

Pseudocódigo do que `POST /reservas` faz em cada modo:

**`none` — sem proteção (de propósito)**
```
se o assento não existe → 404
se já há reserva ativa → 409
espera 50 ms                    ← abre a janela da corrida
grava a reserva                 ← sem índice único: sempre dá certo
```
Entre o "já há reserva?" e o "grava", outra réplica faz a mesma verificação. Resultado: vários `201` para o mesmo assento. A espera de 50 ms só existe para **tornar o erro reproduzível**.

**`db` — o banco decide**
```
grava a reserva
se o índice único recusar → 409
```
Todos chegam ao banco; o Postgres deixa passar um e rejeita o resto.

**`redis` — o cadeado decide**
```
tenta SET lock:assento:{id} NX EX 30
se não conseguiu → 409          ← nem toca no banco
grava a reserva                 ← o índice único continua como segunda barreira
se der erro, libera o cadeado
```
Os 99 perdedores são barrados no Redis, **sem gerar carga no banco**. Por isso este modo teve a menor latência. O índice único segue ativo: se o cadeado expirar (30 s) com a reserva `CONFIRMADA` ainda de pé, o banco continua impedindo a duplicata. É **defesa em camadas**.

| | none | db | redis |
|---|---|---|---|
| Garante consistência? | **Não** | Sim | Sim |
| Quem barra os perdedores | ninguém | Postgres | Redis |
| Carga no banco | alta | alta | baixa |
| Depende de | — | Postgres | Redis + Postgres |

---

## 7. O que acontece quando algo dá errado

A coluna "Verificado?" separa o que foi **testado de verdade** do que apenas **está previsto no código**. Na apresentação, diga com essa clareza.

| Situação | O que o sistema faz | Verificado? |
|---|---|---|
| **Pagamento cai** | A API continua aceitando reservas (ficam `PENDENTE`). As mensagens esperam na fila. Ao voltar, tudo é processado. | **Sim**, no experimento de falha |
| **Pagamento morre no meio de uma mensagem** | Como o `ack` não foi dado, o RabbitMQ devolve a mensagem à fila e ela é reprocessada (`REENTREGA`). A idempotência evita efeito duplicado. | **Sim**, na variante `durante` |
| **Redis cai** | No modo `redis`, `POST /reservas` e `/admin/reset` respondem `503`. No modo `db` o `POST` não usa o Redis. | **Sim** (503 com o Redis parado) |
| **Contêiner cai de verdade** | `restart: unless-stopped` o reinicia. `docker kill` não reinicia (conta como parada manual). | **Sim** (Redis e pagamento) |
| **RabbitMQ cai** | A API não consegue publicar: desfaz a reserva e responde `503`; `connect_robust` reconecta quando ele volta. | Só previsto no código |
| **Postgres cai** | A API deve responder `503`; os dados estão no volume. | Só previsto no código (persistência em `down`/`up` foi verificada) |
| **Uma réplica da API cai** | O nginx tenta outra réplica (`proxy_next_upstream`). | **Não testado** (o cenário extra opcional não foi feito) |
| **O cadeado expira antes do fim** | O índice único do Postgres ainda protege. | Só previsto (o índice único foi exercitado nos modos `db` e `redis`) |
| **Mensagem entregue duas vezes** | O `UPDATE ... WHERE status='PENDENTE'` ignora a segunda. | Previsto; a reentrega ocorreu, mas a reserva ainda estava `PENDENTE` |
| **Pagamento recusado** | A reserva vira `RECUSADA`, o assento volta a ficar livre e o cadeado é apagado. | Parcial: houve recusas, mas não conferimos a liberação do assento |

---

## 8. Os experimentos: o que medem e como ler

### Experimento 1 — Concorrência
- **Pergunta:** o que acontece quando 100 pedidos disputam o mesmo assento?
- **Como:** para cada modo, `reset`, 100 requisições simultâneas, contagem de `201`/`409`, latência e consulta de overbooking. Repetido 3 vezes.
- **Resultado:**

| Modo | Aceitas | Overbooking | Latência média |
|---|---|---|---|
| none | 18,7 | sim (3 de 3) | 538 ms |
| db | 1 | não | 463 ms |
| redis | 1 | não | 314 ms |

- **Como explicar:** sem controle, quase 19 pessoas "compraram" o mesmo lugar. Banco e Redis resolvem. O Redis é mais rápido porque os perdedores nem chegam ao banco.

### Experimento 2 — Escalabilidade
- **Pergunta:** mais réplicas da API aumentam a vazão?
- **Como:** 500 reservas em assentos diferentes, 50 conexões simultâneas, com 1, 2 e 3 réplicas.
- **Resultado:** 132 req/s com 1 réplica; cerca de 92 req/s com 2 e com 3. **Não houve ganho.**
- **Como explicar (com honestidade):** o balanceamento funciona (a carga se dividiu quase igual) e a CPU dos contêineres ficou perto de 0,2%, então a API não era o gargalo. **A causa provável** é o gerador de carga em Python e a rede entre o Windows e o WSL2, tudo na mesma máquina. **Isso é uma hipótese, não foi medida.** Para confirmar, rodaríamos o gerador dentro da rede Docker.

### Experimento de falha — queda do pagamento
- **Pergunta:** reservas se perdem se o pagamento cair?
- **Resultado:** não. Com o serviço parado, 20 reservas ficaram `PENDENTE` e a fila acumulou 20 mensagens; ao religar, todas foram processadas. Na variante em que o serviço é morto no meio, a mensagem voltou à fila e foi reprocessada. Os prints do RabbitMQ estão em `evidencias/falha/`.

---

## 9. Perguntas que podem fazer na apresentação

**Por que usar Redis se o banco já impede o overbooking?**
Porque o banco recebe todos os pedidos e rejeita o excesso, o que custa. O Redis barra os perdedores antes, aliviando o banco, e foi mais rápido no experimento. O índice do banco continua como segunda barreira.

**Se o Redis cair, o sistema vende o mesmo assento duas vezes?**
Não. O modo `redis` responde `503`, e mesmo que o lock falhasse o índice único do Postgres impediria a duplicata. O ponto fraco é a **disponibilidade**, e não a consistência.

**O lock de 30 segundos pode expirar no meio da operação?**
Pode. Se a gravação demorar mais de 30 s, outra requisição pega o cadeado. Mesmo assim o índice único recusa a duplicata. É a razão de manter as duas defesas.

**Por que o pagamento é assíncrono?**
Para a reserva responder rápido e para o sistema tolerar a queda do pagamento sem perder pedidos.

**O que garante que nenhuma reserva se perde?**
Fila durável, mensagens persistentes e ack manual só depois de gravar no banco, mais o volume do Postgres.

**O que é idempotência e por que importa aqui?**
É poder repetir uma operação sem efeito extra. Como o RabbitMQ pode entregar a mesma mensagem duas vezes, o `UPDATE` só age se a reserva ainda estiver `PENDENTE`.

**Por que mais réplicas não aumentaram a vazão?**
Medimos que a CPU da API ficou ociosa e que o balanceamento funcionou, então o gargalo estava fora da API. A causa provável é o gerador de carga e a rede do Docker Desktop no Windows, mas não confirmamos.

**Para que serve o atraso de 50 ms no modo `none`?**
Para ampliar a janela da condição de corrida e tornar o overbooking reproduzível. Sem ele, o erro aconteceria com menos frequência, mas ainda aconteceria.

**Por que a API não guarda estado?**
Porque assim qualquer réplica atende qualquer pedido e dá para escalar horizontalmente.

**O que mudaríamos para produção?**
Redis e Postgres replicados, Redlock, orquestração (Kubernetes), métricas (Prometheus e Grafana) e um gateway de pagamento real.

---

## 10. Glossário

| Termo | Significado |
|---|---|
| **Overbooking** | Vender o mesmo assento a mais de uma pessoa |
| **Condição de corrida** | Erro causado por duas operações concorrentes sobre uma verificação desatualizada |
| **Lock / cadeado** | Marca que impede outros de mexer no mesmo recurso ao mesmo tempo |
| **TTL** | Tempo de vida: depois dele, o dado expira sozinho |
| **Réplica** | Cópia idêntica de um serviço rodando em paralelo |
| **Balanceador** | Distribui as requisições entre as réplicas (aqui, o nginx) |
| **Fila / broker** | Caixa de recados entre programas (aqui, o RabbitMQ) |
| **Consumidor** | Programa que lê as mensagens da fila (o `pagamento-service`) |
| **Ack** | Confirmação de que a mensagem foi processada |
| **Idempotência** | Repetir a operação não muda o resultado |
| **Índice único parcial** | Regra do banco que proíbe duplicatas só para linhas que atendem a uma condição |
| **Pool de conexões** | Grupo de conexões ao banco reaproveitadas |
| **Healthcheck** | Teste que diz se um serviço está de fato pronto |
| **Volume** | Armazenamento que sobrevive à remoção do contêiner |
| **Latência p95** | Tempo abaixo do qual ficam 95% das respostas |
| **Vazão (req/s)** | Quantas requisições são atendidas por segundo |
