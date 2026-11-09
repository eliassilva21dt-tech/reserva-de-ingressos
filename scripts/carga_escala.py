"""Experimento 2: escalabilidade da API (N reservas em assentos DIFERENTES).

Uso:
    python scripts/carga_escala.py --replicas 2 --rep 1 [--reqs 500] [--conc 50]

--replicas é só um rótulo para o nome do arquivo (quem escala é o docker compose).
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

from carga_concorrencia import percentil


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--reqs", type=int, default=500)
    ap.add_argument("--conc", type=int, default=50)
    ap.add_argument("--replicas", type=int, required=True)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=30, help="requisições de aquecimento")
    ap.add_argument("--out", default="evidencias/exp2")
    args = ap.parse_args()

    limites = httpx.Limits(max_connections=args.conc, max_keepalive_connections=args.conc)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limites, timeout=60) as cli:
        # Aquecimento (conexões, pools, caches) - descartado do resultado.
        (await cli.post("/admin/reset")).raise_for_status()
        modo = (await cli.get("/admin/stats")).json()["modo"]
        await asyncio.gather(*(cli.get("/health") for _ in range(args.warmup)))
        (await cli.post("/admin/reset")).raise_for_status()

        sem = asyncio.Semaphore(args.conc)
        resultados: list[tuple[int, float, str | None]] = []

        async def disparar(i: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await cli.post(
                        "/reservas", json={"assento_id": i + 1, "cliente": f"cli{i}"}
                    )
                    codigo = r.status_code
                    inst = r.json().get("instancia") if codigo == 201 else None
                except httpx.HTTPError:
                    codigo, inst = 0, None
                resultados.append((codigo, (time.perf_counter() - t0) * 1000, inst))

        t_ini = time.perf_counter()
        await asyncio.gather(*(disparar(i) for i in range(args.reqs)))
        total = time.perf_counter() - t_ini

        stats = (await cli.get("/admin/stats")).json()

    codigos = Counter(c for c, _, _ in resultados)
    lat = [ms for _, ms, _ in resultados]
    por_instancia = Counter(i for _, _, i in resultados if i)
    resumo = {
        "experimento": "exp2_escalabilidade",
        "modo": modo,
        "replicas": args.replicas,
        "repeticao": args.rep,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "requisicoes": args.reqs,
        "concorrencia": args.conc,
        "tempo_total_s": round(total, 3),
        "req_por_s": round(args.reqs / total, 1),
        "lat_media_ms": round(statistics.mean(lat), 1),
        "lat_p95_ms": round(percentil(lat, 95), 1),
        "codigos": dict(codigos),
        "distribuicao_por_instancia": dict(por_instancia),
        "overbooking": stats["overbooking"],
    }

    saida = Path(args.out)
    saida.mkdir(parents=True, exist_ok=True)
    arq = saida / f"exp2_{args.replicas}rep_rep{args.rep}_{datetime.now():%Y%m%d-%H%M%S}.json"
    arq.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    print(f"\nsalvo em {arq}")


if __name__ == "__main__":
    asyncio.run(main())
