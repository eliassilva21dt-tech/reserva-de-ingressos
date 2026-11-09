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
