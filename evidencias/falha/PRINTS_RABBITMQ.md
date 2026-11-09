# Prints do painel do RabbitMQ (experimento de falha: pagamento parado)

Painel: http://localhost:15672, fila `reservas.pagamento`. Capturados em 09/11/2026 durante uma execução
manual do mesmo procedimento de `scripts/falha_pagamento.sh` (parar o pagamento, criar 20 reservas, religar).

| Print | Horário | O que mostra |
|---|---|---|
| `rabbitmq_1_fila_vazia_...jpg` | 15:40:12 | Estado inicial: fila vazia (0 prontas, 0 unacked), 1 consumidor, estado `idle`. |
| `rabbitmq_2_pagamento_parado_20_prontas_...jpg` | 15:40:32 | Pagamento parado e 20 reservas criadas: **20 prontas**, 0 unacked, **0 consumidores**. As 20 mensagens são persistentes (702 B). |
| `rabbitmq_3_drenando_unacked_...jpg` | 15:40:57 | Pagamento religado: 14 prontas, **1 unacked** (a mensagem em processamento), 1 consumidor, ack a 0,40/s. |
| `rabbitmq_4_fila_zerada_...jpg` | 15:41:32 | Fila zerada (0 prontas, 0 unacked). O gráfico "Queued messages" mostra a descida de 20 para 0. |

Resultado final (`GET /reservas/{id}` nas 20 reservas): 19 CONFIRMADA e 1 RECUSADA. Nenhuma reserva perdida.
