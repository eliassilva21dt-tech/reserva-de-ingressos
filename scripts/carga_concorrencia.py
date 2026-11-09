"""Experimento 1: N requisições simultâneas ao MESMO assento.

Uso:
    python scripts/carga_concorrencia.py --rep 1 [--reqs 100] [--assento 10]

O modo (none|db|redis) é definido na API (MODO_LOCK); o script apenas lê qual
está ativo em /admin/stats e grava o resultado em evidencias/exp1/.
"""
import argparse
import asyncio
import json
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    idx = min(len(ordenados) - 1, max(0, round(p / 100 * len(ordenados)) - 1))
    return ordenados[idx]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--reqs", type=int, default=100)
    ap.add_argument("--assento", type=int, default=10)
    ap.add_argument("--rep", type=int, default=1, help="número da repetição")
    ap.add_argument("--out", default="evidencias/exp1")
    args = ap.parse_args()

    limites = httpx.Limits(max_connections=args.reqs, max_keepalive_connections=args.reqs)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limites, timeout=60) as cli:
        (await cli.post("/admin/reset")).raise_for_status()
        modo = (await cli.get("/admin/stats")).json()["modo"]

        # Aquece as conexões para que as N requisições saiam realmente juntas.
        await asyncio.gather(*(cli.get("/health") for _ in range(args.reqs)))

        largada = asyncio.Event()
        resultados: list[tuple[int, float, str | None]] = []

        async def disparar(i: int) -> None:
            await largada.wait()
            t0 = time.perf_counter()
            try:
                r = await cli.post(
                    "/reservas", json={"assento_id": args.assento, "cliente": f"cli{i}"}
                )
                codigo = r.status_code
                inst = r.json().get("instancia") if codigo == 201 else None
            except httpx.HTTPError:
                codigo, inst = 0, None
            resultados.append((codigo, (time.perf_counter() - t0) * 1000, inst))

        tarefas = [asyncio.create_task(disparar(i)) for i in range(args.reqs)]
        await asyncio.sleep(0.2)
        t_ini = time.perf_counter()
        largada.set()
        await asyncio.gather(*tarefas)
        duracao = time.perf_counter() - t_ini

        stats = (await cli.get("/admin/stats")).json()

    codigos = Counter(c for c, _, _ in resultados)
    lat = [ms for _, ms, _ in resultados]
    resumo = {
        "experimento": "exp1_concorrencia",
        "modo": modo,
        "repeticao": args.rep,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "requisicoes": args.reqs,
        "assento_id": args.assento,
        "aceitas_201": codigos.get(201, 0),
        "conflito_409": codigos.get(409, 0),
        "erros": sum(n for c, n in codigos.items() if c not in (201, 409)),
        "codigos": dict(codigos),
        "lat_media_ms": round(statistics.mean(lat), 1),
        "lat_p95_ms": round(percentil(lat, 95), 1),
        "duracao_s": round(duracao, 3),
        "overbooking": stats["overbooking"],
        "houve_overbooking": bool(stats["overbooking"]),
        "reservas_por_status": stats["reservas_por_status"],
    }

    saida = Path(args.out)
    saida.mkdir(parents=True, exist_ok=True)
    arq = saida / f"exp1_{modo}_rep{args.rep}_{datetime.now():%Y%m%d-%H%M%S}.json"
    arq.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    print(f"\nsalvo em {arq}")


if __name__ == "__main__":
    asyncio.run(main())
