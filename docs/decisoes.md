# Decisões técnicas

Uma linha por decisão: **decisão — motivo**.

- **Python 3.12 + FastAPI + asyncpg** para a API — simples, assíncrono (aguenta muitas conexões simultâneas) e com boa documentação.
- **aio-pika** (RabbitMQ) na API e no pagamento — mesma família assíncrona; `connect_robust` reconecta sozinho se o broker cair.
- **Postgres com índice único parcial** (`uq_reserva_ativa`) — a restrição do banco é a barreira final contra overbooking, mesmo se o lock falhar.
- **Redis `SET NX EX`** como lock por assento — barra a disputa antes de chegar ao banco; o TTL evita prender o assento se a API morrer.
- **Liberação do lock na API com Lua (compare-and-delete)** — só apaga se o token for da própria requisição.
- **Pagamento apaga o lock com `DEL` simples** — a mensagem da fila só carrega `reserva_id` e `assento_id` (contrato do PROJETO.md); a recusa libera o assento. Limitação conhecida e aceita.
- **Índice comum `idx_reservas_assento` no `init.sql`** (além do PROJETO.md) — acelera "assento tem reserva ativa?" também no modo `none`, que não tem o índice único.
- **Se a publicação na fila falha, a API apaga a reserva e devolve 503** — sem fila ninguém processaria o pagamento; evita reserva PENDENTE eterna.
- **`TRUNCATE ... RESTART IDENTITY` + purge da fila no `/admin/reset`** — cada rodada de experimento começa limpa e com ids previsíveis.
- **nginx com `resolver 127.0.0.11 valid=5s` e `proxy_pass $upstream`** — o nginx re-resolve o DNS de `api` a cada 5 s, então `--scale api=N` funciona sem reiniciar o proxy.
- **`deploy.replicas: 2` no Compose** — a stack já sobe com 2 réplicas da API; `--scale api=N` sobrescreve.
- **Healthcheck da API com `python urllib`** — a imagem `python:slim` não tem `curl`.
- **Rede `back-net` não é `internal`** — para o painel do RabbitMQ (`15672`) poder ser publicado no host durante o desenvolvimento.
- **Teste de `restart: unless-stopped` com queda real, não com `docker kill`** — no Docker atual, `docker kill`/`docker stop` contam como parada manual e a política NÃO reinicia o contêiner. Verificado: `docker kill` deixa o contêiner em `Exited (137)`; já uma queda real (`redis-cli shutdown`, SIGINT no PID 1 do pagamento) reinicia sozinha (`RestartCount=1`). O critério de aceite da Fase 7 do PROJETO.md foi ajustado para isso.
- **Scripts de carga com `httpx` + `asyncio`** — gera concorrência real a partir de um único processo e mede latência por requisição.
- **Cabeçalho `Server-Timing` nas respostas de `POST /reservas`** — padrão HTTP; a API informa quanto tempo gastou em cada etapa (lock, banco, fila). Sem isso não dá para separar o tempo do sistema do tempo do gerador de carga. Não muda o corpo das respostas.
- **CPU medida pelo contador do contêiner (`cpu.stat`, antes e depois da carga)** em vez de `docker stats` — o `docker stats` só mostra o instante da amostra; na primeira bateria ele foi tirado depois do fim da carga e levou à conclusão errada de que a API estava ociosa.
- **Experimentos refeitos com aquecimento, 6 repetições e ordem alternada** — na primeira bateria (3 repetições, ordem fixa) a primeira rodada após recriar a API saía mais lenta e distorcia as médias. A bateria antiga foi mantida em `evidencias/exp1/v1` e `evidencias/exp2/v1`.
- **Experimento 2 com duas cargas (leve e forte)** — a carga do PROJETO.md (500 reservas, 50 conexões, 1 processo) é limitada pelo gerador de carga em ~100 req/s e não ocupa a API. A carga forte (1000 reservas, 96 conexões, 8 processos do gerador) existe para a carga chegar de verdade ao sistema. Continua sendo Python + httpx, sem ferramenta nova.
- **Experimento 1 compara os modos pelo tempo dentro da API**, e não pela latência vista pelo cliente — com 100 pedidos disparados por um único processo, a latência do cliente mede principalmente o gerador de carga.
- **Volume `rabbitmq_data` e `hostname: rabbitmq` no Compose** — o RabbitMQ guarda os dados numa pasta com o nome do nó (`rabbit@<hostname>`). Sem volume e sem hostname fixo, um `docker compose down`/`up` apagava as mensagens da fila e as reservas ficavam `PENDENTE` para sempre. Verificado com `scripts/falha_reinicio.sh`, antes e depois.
- **Reserva `PENDENTE` sem expiração e sem fila de erros (DLQ)** — limitações conhecidas e aceitas; ficam registradas no relatório em vez de mudar a lógica central na véspera da entrega.
- **Experimentos extras (`falha_api.py`, `escala_pagamento.py`)** — o cenário opcional do PROJETO.md (derrubar uma réplica da API com carga) e a escala do consumidor, onde o limite é o atraso do pagamento e não a CPU.
