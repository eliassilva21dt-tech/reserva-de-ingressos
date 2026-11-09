# Reserva de Ingressos com Controle de Concorrência

Trabalho semestral de Computação Distribuída (UFMS) — Alessandro, Anna Júlia e Elias.
Detalhes de projeto, contratos e fases: [`PROJETO.md`](PROJETO.md).

## Pré-requisitos
- Docker Desktop (com `docker compose`)
- Python 3.12+ (apenas para os scripts de carga)
- Git Bash (Windows) ou qualquer shell POSIX para os `.sh`

## Subir a aplicação
```bash
cp .env.example .env
docker compose up -d --build
bash scripts/aguardar_api.sh
curl http://localhost:8080/health      # {"status":"ok","instancia":"<hostname>"}
```
Painel do RabbitMQ: http://localhost:15672 (guest / guest).

## Teste rápido
```bash
curl http://localhost:8080/eventos/1/assentos | head -c 200
curl -X POST http://localhost:8080/reservas -H 'Content-Type: application/json' \
     -d '{"assento_id": 10, "cliente": "elias"}'
curl http://localhost:8080/reservas/1        # PENDENTE -> CONFIRMADA/RECUSADA em 1-3 s
for i in 1 2 3 4; do curl -s http://localhost:8080/health; echo; done   # hostnames alternando
```

## Preparar os scripts
```bash
python -m venv .venv && source .venv/Scripts/activate   # Linux/Mac: .venv/bin/activate
pip install -r scripts/requirements.txt
```

## Experimentos (saídas em `evidencias/`)
| Experimento | Comando | Saída |
|---|---|---|
| 1 — Concorrência (3 modos × 3 rep.) | `bash scripts/rodar_exp1.sh` | `evidencias/exp1/*.json` + tabela |
| 2 — Escalabilidade (1/2/3 réplicas × 3 rep.) | `bash scripts/rodar_exp2.sh` | `evidencias/exp2/*.json` + tabela |
| Falha — queda do pagamento | `bash scripts/falha_pagamento.sh` | `evidencias/falha/*.log` |
| Falha — queda durante o processamento (reentrega) | `bash scripts/falha_pagamento.sh durante` | `evidencias/falha/*.log` |
| Coletar logs/estado da stack | `bash scripts/coletar_evidencias.sh exp1` | `evidencias/exp1/` |

Tabelas resumidas a qualquer momento: `python scripts/resumir.py exp1` / `exp2`.
Tire também prints do painel do RabbitMQ e guarde em `evidencias/`.

Trocar o modo de controle manualmente: `MODO_LOCK=none docker compose up -d --force-recreate api`
(`none` | `db` | `redis`). Estado/overbooking: `curl http://localhost:8080/admin/stats`.

## Parar e limpar
```bash
docker compose down        # mantém os dados (volume pgdata)
docker compose down -v     # apaga também o banco
```

## Estrutura
Ver seção 8 do [`PROJETO.md`](PROJETO.md). Decisões técnicas: [`docs/decisoes.md`](docs/decisoes.md). Rascunho do relatório: [`docs/relatorio.md`](docs/relatorio.md).
