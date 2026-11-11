"""Experimento extra: escalar o pagamento-service (mais consumidores na fila).

Uso (quem escala é o docker compose):
    docker compose up -d --scale pagamento=2 pagamento
    python scripts/escala_pagamento.py --consumidores 2 --rep 1

Cria --reservas reservas de uma vez e mede quanto tempo os consumidores levam
para processar todas (nenhuma PENDENTE). Cada pagamento leva de 1 a 3 s
(simulado), então aqui o limite é o número de consumidores, e não a CPU.
Resultado em evidencias/escala_pagamento/.
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx


def consumidores_na_fila() -> int | None:
    """Quantos consumidores o RabbitMQ enxerga na fila (confere o --scale)."""
    try:
        saida = subprocess.run(
            ["docker", "compose", "exec", "-T", "rabbitmq", "rabbitmqctl", "-q",
             "list_queues", "name", "consumers"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for linha in saida.splitlines():
            if linha.startswith("reservas.pagamento"):
                return int(linha.split()[1])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--consumidores", type=int, required=True)
    ap.add_argument("--reservas", type=int, default=40)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--out", default="evidencias/escala_pagamento")
    args = ap.parse_args()

    vistos = consumidores_na_fila()
    if vistos is not None and vistos != args.consumidores:
        sys.exit(f"ERRO: a fila tem {vistos} consumidor(es), esperava {args.consumidores}. Nada foi salvo.")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as cli:
        (await cli.post("/admin/reset")).raise_for_status()
        await asyncio.sleep(4)  # deixa terminar um pagamento que estivesse em curso

        respostas = await asyncio.gather(*(
            cli.post("/reservas", json={"assento_id": i + 1, "cliente": f"cli{i}"})
            for i in range(args.reservas)
        ))
        aceitas = sum(r.status_code == 201 for r in respostas)
        t0 = time.perf_counter()

        while True:
            por_status = (await cli.get("/admin/stats")).json()["reservas_por_status"]
            if por_status.get("PENDENTE", 0) == 0:
                break
            if time.perf_counter() - t0 > 600:
                sys.exit("ERRO: as reservas não foram processadas em 600 s.")
            await asyncio.sleep(0.25)
        tempo = time.perf_counter() - t0

    resumo = {
        "experimento": "escala_pagamento",
        "consumidores": args.consumidores,
        "consumidores_vistos_pelo_rabbitmq": vistos,
        "repeticao": args.rep,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "reservas": args.reservas,
        "aceitas_201": aceitas,
        "tempo_para_processar_s": round(tempo, 1),
        "pagamentos_por_s": round(args.reservas / tempo, 2),
        "reservas_por_status": por_status,
    }
    pasta = Path(args.out)
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"escala_pagamento_{args.consumidores}cons_rep{args.rep}_{datetime.now():%Y%m%d-%H%M%S}.json"
    arq.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    print(f"\nsalvo em {arq}")


if __name__ == "__main__":
    asyncio.run(main())
