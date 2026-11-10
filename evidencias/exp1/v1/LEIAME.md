# Experimento 1 — primeira bateria (substituída)

Medição de 20/10/2026: 3 repetições por modo, ordem fixa (`none`, `db`, `redis`) e aquecimento só com `GET /health`.

Continua válido: a contagem de reservas aceitas (overbooking no modo `none` nas 3 rodadas; exatamente 1 aceita em `db` e `redis`).

Não usar: a comparação de latência entre os modos ("redis 314 ms contra db 463 ms"). A primeira rodada de cada modo, feita logo depois de recriar a API, saiu bem mais lenta (none 906 ms, db 645 ms) e puxou as médias. Além disso, a latência vista pelo cliente mede principalmente o gerador de carga.

A bateria atual está na pasta de cima (`evidencias/exp1/`). O `resumir.py` não lê esta pasta.
