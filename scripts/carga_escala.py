"""Experimento 2: escalabilidade da API (N reservas em assentos DIFERENTES).

Uso:
    python scripts/carga_escala.py --replicas 2 --rep 1 [--reqs 500] [--conc 50] [--processos 1]

--replicas é o número de réplicas que DEVERIA estar no ar (quem escala é o
docker compose). O script confere, no aquecimento, se esse número de instâncias
está mesmo respondendo antes de medir.

--processos divide a carga entre vários processos do gerador. Um único processo
Python não consegue gerar pedidos rápido o bastante para ocupar a API (ele vira
o gargalo); com vários processos a carga chega de verdade ao sistema.

Além de vazão e latência, cada rodada registra:
  - CPU gasta por serviço DURANTE a carga (scripts/medir_cpu.py);
  - tempo médio de cada etapa dentro da API (cabeçalho Server-Timing).
"""
import argparse
import asyncio
import json
import multiprocessing as mp
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx

import medir_cpu
from carga_concorrencia import percentil


def ler_server_timing(cabecalho: str | None) -> dict[str, float]:
    """'controle;dur=1.2, banco;dur=3.4' -> {'controle': 1.2, 'banco': 3.4}"""
    tempos = {}
    for parte in (cabecalho or "").split(","):
        nome, _, dur = parte.strip().partition(";dur=")
        if dur:
            tempos[nome] = float(dur)
    return tempos


async def reservar(cli: httpx.AsyncClient, sem: asyncio.Semaphore, assento_id: int, destino: list) -> None:
    async with sem:
        t0 = time.perf_counter()
        try:
            r = await cli.post("/reservas", json={"assento_id": assento_id, "cliente": f"cli{assento_id}"})
            codigo = r.status_code
            inst = r.json().get("instancia") if codigo == 201 else None
            etapas = ler_server_timing(r.headers.get("server-timing"))
        except httpx.HTTPError:
            codigo, inst, etapas = 0, None, {}
        destino.append((codigo, (time.perf_counter() - t0) * 1000, inst, etapas))


def trabalhador(base_url: str, assentos: list[int], conc: int, pronto, largada, saida) -> None:
    """Um processo do gerador: abre as conexões, espera a largada e dispara."""

    async def rodar() -> None:
        limites = httpx.Limits(max_connections=conc, max_keepalive_connections=conc)
        async with httpx.AsyncClient(base_url=base_url, limits=limites, timeout=60) as cli:
            await asyncio.gather(*(cli.get("/health") for _ in range(conc)))  # abre as conexões
            pronto.put(1)
            await asyncio.to_thread(largada.wait)
            sem = asyncio.Semaphore(conc)
            resultados: list = []
            await asyncio.gather(*(reservar(cli, sem, a, resultados) for a in assentos))
            saida.put((time.time(), resultados))

    asyncio.run(rodar())


async def aquecer(args) -> str:
    """Reservas de verdade (descartadas) para abrir as conexões nginx->API->Postgres
    em todas as réplicas e conferir se o número esperado de instâncias responde."""
    limites = httpx.Limits(max_connections=args.conc, max_keepalive_connections=args.conc)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limites, timeout=60) as cli:
        sem = asyncio.Semaphore(args.conc)
        for tentativa in range(1, 7):
            (await cli.post("/admin/reset")).raise_for_status()
            modo = (await cli.get("/admin/stats")).json()["modo"]
            aquec: list = []
            await asyncio.gather(*(reservar(cli, sem, i + 1, aquec) for i in range(args.warmup)))
            vistas = {inst for _, _, inst, _ in aquec if inst}
            if len(vistas) == args.replicas:
                break
            print(f"aquecimento {tentativa}: {len(vistas)} instância(s) respondendo, "
                  f"esperava {args.replicas}; aguardando o proxy...", file=sys.stderr)
            await asyncio.sleep(5)
        else:
            sys.exit(f"ERRO: {len(vistas)} instância(s) no ar, esperava {args.replicas}. Nada foi salvo.")
        (await cli.post("/admin/reset")).raise_for_status()
    return modo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--reqs", type=int, default=500)
    ap.add_argument("--conc", type=int, default=50, help="conexões simultâneas (total)")
    ap.add_argument("--processos", type=int, default=1, help="processos do gerador de carga")
    ap.add_argument("--replicas", type=int, required=True)
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=150, help="reservas de aquecimento (descartadas)")
    ap.add_argument("--out", default="evidencias/exp2")
    ap.add_argument("--rotulo", default="", help="texto extra no nome do arquivo (ex.: 4proc)")
    args = ap.parse_args()

    modo = asyncio.run(aquecer(args))
    time.sleep(1)

    # ---- Prepara os processos do gerador (assentos repartidos entre eles) ------
    pronto, saida, largada = mp.Queue(), mp.Queue(), mp.Event()
    conc_por_proc = max(1, args.conc // args.processos)
    todos = list(range(1, args.reqs + 1))
    procs = [
        mp.Process(
            target=trabalhador,
            args=(args.base_url, todos[p::args.processos], conc_por_proc, pronto, largada, saida),
        )
        for p in range(args.processos)
    ]
    for p in procs:
        p.start()
    for _ in procs:
        pronto.get(timeout=60)

    # ---- Medição -----------------------------------------------------------------
    containers = medir_cpu.listar_containers() if medir_cpu.disponivel() else {}
    cpu_antes = medir_cpu.foto(containers) if containers else {}

    t_ini = time.time()
    largada.set()
    retornos = [saida.get(timeout=300) for _ in procs]
    total = max(t_fim for t_fim, _ in retornos) - t_ini

    cpu_depois = medir_cpu.foto(containers) if containers else {}
    for p in procs:
        p.join(timeout=10)
    stats = httpx.get(f"{args.base_url}/admin/stats", timeout=30).json()

    resultados = [r for _, rs in retornos for r in rs]
    codigos = Counter(c for c, _, _, _ in resultados)
    lat = [ms for _, ms, _, _ in resultados]
    por_instancia = Counter(i for _, _, i, _ in resultados if i)

    etapas_soma: dict[str, list[float]] = defaultdict(list)
    for _, _, _, etapas in resultados:
        for nome, ms in etapas.items():
            etapas_soma[nome].append(ms)
    etapas_media = {nome: round(statistics.mean(v), 1) for nome, v in etapas_soma.items()}
    lat_media = round(statistics.mean(lat), 1)

    resumo = {
        "experimento": "exp2_escalabilidade",
        "modo": modo,
        "replicas": args.replicas,
        "repeticao": args.rep,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "requisicoes": args.reqs,
        "concorrencia": conc_por_proc * args.processos,
        "processos_gerador": args.processos,
        "aquecimento": args.warmup,
        "tempo_total_s": round(total, 3),
        "req_por_s": round(args.reqs / total, 1),
        "lat_media_ms": lat_media,
        "lat_p95_ms": round(percentil(lat, 95), 1),
        "codigos": dict(codigos),
        "distribuicao_por_instancia": dict(por_instancia),
        # Tempo médio de cada etapa DENTRO da API (ms) e o que sobra fora dela
        # (proxy, rede e o próprio gerador de carga).
        "etapas_api_ms": etapas_media,
        "fora_da_api_ms": round(lat_media - etapas_media["total"], 1) if "total" in etapas_media else None,
        # % de um núcleo, média durante a carga (100 = um núcleo inteiro).
        "cpu_durante_carga_pct": medir_cpu.resumo(containers, cpu_antes, cpu_depois, total),
        "nucleos_do_docker": medir_cpu.nucleos_do_docker() if containers else None,
        "overbooking": stats["overbooking"],
    }

    pasta = Path(args.out)
    pasta.mkdir(parents=True, exist_ok=True)
    rotulo = f"_{args.rotulo}" if args.rotulo else ""
    arq = pasta / f"exp2_{args.replicas}rep{rotulo}_rep{args.rep}_{datetime.now():%Y%m%d-%H%M%S}.json"
    arq.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    print(f"\nsalvo em {arq}")


if __name__ == "__main__":
    main()
