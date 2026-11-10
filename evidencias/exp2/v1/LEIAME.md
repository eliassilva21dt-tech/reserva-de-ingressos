# Experimento 2 — primeira bateria (substituída)

Medição de 20/10/2026: 3 repetições por configuração, ordem fixa (1, 2, 3 réplicas), 500 reservas com 50 conexões e um único processo gerando carga.

Não usar as conclusões tiradas desta bateria:

- "A CPU ficou perto de 0,2%, então a API não é o gargalo": o `docker stats` foi tirado depois do fim da carga, com tudo parado.
- "1 réplica rende mais (132 contra 92 req/s)": as rodadas com 1 réplica deram 156, 97 e 143 req/s; a diferença está dentro da variação.

O que estes números mostram, à luz da bateria atual: com um processo só, o gerador de carga limita o teste em ~100 req/s, com qualquer número de réplicas.

A bateria atual está na pasta de cima (`evidencias/exp2/`). O `resumir.py` não lê esta pasta.
