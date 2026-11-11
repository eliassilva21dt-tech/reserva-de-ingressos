"""Experimento extra de falha: derrubar UMA réplica da API com carga contínua.

Uso (stack no ar com 2 réplicas da API):
    python scripts/falha_api.py --modo stop
    python scripts/falha_api.py --modo kill

Envia reservas a uma taxa fixa durante --duracao segundos. No instante
--derrubar-em uma réplica é parada (`docker stop` = encerramento educado;
`docker kill` = morte abrupta) e em --religar-em ela é ligada de novo.
Mede: erros, latência e quanto tempo o proxy leva para voltar a usar a réplica.
Resultado em evidencias/falha/falha_api_<modo>_<data>.json.
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

import medir_cpu
from carga_concorrencia import percentil


def docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60).stdout.strip()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument("--modo", choices=["stop", "kill"], default="stop")
    ap.add_argument("--duracao", type=float, default=36.0, help="segundos de carga")
    ap.add_argument("--taxa", type=float, default=25.0, help="reservas por segundo")
    ap.add_argument("--derrubar-em", type=float, default=12.0)
    ap.add_argument("--religar-em", type=float, default=24.0)
    ap.add_argument("--out", default="evidencias/falha")
    args = ap.parse_args()

    if not medir_cpu.disponivel():
        sys.exit("ERRO: o comando 'docker' precisa estar no PATH.")
    replicas = sorted(n for n, s in medir_cpu.listar_containers().items() if s == "api")
    if len(replicas) != 2:
        sys.exit(f"ERRO: esperava 2 réplicas da API no ar, encontrei {len(replicas)}.")
    vitima = replicas[0]
    # O campo "instancia" das respostas é o hostname do contêiner.
    host_vitima = docker("inspect", "-f", "{{.Config.Hostname}}", vitima)

    total = int(args.duracao * args.taxa)
    eventos: dict[str, float] = {}
    resultados: list[tuple[float, int, float, str | None]] = []  # (envio_s, código, ms, instância)

    limites = httpx.Limits(max_connections=100, max_keepalive_connections=100)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limites, timeout=15) as cli:
        (await cli.post("/admin/reset")).raise_for_status()
        vistas = {(await cli.get("/health")).json()["instancia"] for _ in range(20)}
        if len(vistas) != 2:
            sys.exit(f"ERRO: o proxy está usando {len(vistas)} instância(s); esperava 2.")

        t0 = time.perf_counter()

        async def reservar(i: int) -> None:
            await asyncio.sleep(max(0.0, t0 + i / args.taxa - time.perf_counter()))
            envio = time.perf_counter() - t0
            try:
                r = await cli.post("/reservas", json={"assento_id": i + 1, "cliente": f"cli{i}"})
                codigo = r.status_code
                inst = r.json().get("instancia") if codigo == 201 else None
            except httpx.HTTPError:
                codigo, inst = 0, None
            resultados.append((envio, codigo, (time.perf_counter() - t0 - envio) * 1000, inst))

        async def acao(quando: float, nome: str, *cmd: str) -> None:
            await asyncio.sleep(max(0.0, t0 + quando - time.perf_counter()))
            eventos[f"{nome}_inicio_s"] = round(time.perf_counter() - t0, 2)
            await asyncio.to_thread(docker, *cmd)
            eventos[f"{nome}_fim_s"] = round(time.perf_counter() - t0, 2)

        await asyncio.gather(
            *(reservar(i) for i in range(total)),
            acao(args.derrubar_em, "derrubar", args.modo, vitima),
            acao(args.religar_em, "religar", "start", vitima),
        )
        stats = (await cli.get("/admin/stats")).json()

    resultados.sort()
    codigos = Counter(c for _, c, _, _ in resultados)
    lat = [ms for _, _, ms, _ in resultados]
    t_cai, t_volta = eventos["derrubar_inicio_s"], eventos["religar_inicio_s"]
    fases = {
        "antes": [r for r in resultados if r[0] < t_cai],
        "replica_fora": [r for r in resultados if t_cai <= r[0] < t_volta],
        "depois_de_religar": [r for r in resultados if r[0] >= t_volta],
    }

    def resumo_fase(rs: list) -> dict:
        ls = [ms for _, _, ms, _ in rs]
        return {
            "requisicoes": len(rs),
            "erros": sum(1 for _, c, _, _ in rs if c != 201),
            "lat_media_ms": round(sum(ls) / len(ls), 1) if ls else None,
            "lat_p95_ms": round(percentil(ls, 95), 1) if ls else None,
            "lat_max_ms": round(max(ls), 1) if ls else None,
            "por_instancia": dict(Counter(i for _, _, _, i in rs if i)),
        }

    # Última resposta da vítima depois de derrubada e primeira depois de religada.
    da_vitima = [envio for envio, _, _, inst in resultados if inst == host_vitima]
    ultima_antes = max((e for e in da_vitima if e < t_volta), default=None)
    primeira_depois = min((e for e in da_vitima if e >= t_volta), default=None)
    lentas = [(round(e, 2), round(ms)) for e, _, ms, _ in resultados if ms > 500]

    resumo = {
        "experimento": "falha_replica_api",
        "modo_da_queda": f"docker {args.modo}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "taxa_req_por_s": args.taxa,
        "duracao_s": args.duracao,
        "requisicoes": total,
        "replica_derrubada": {"container": vitima, "instancia": host_vitima},
        "eventos_s": eventos,
        "codigos": dict(codigos),
        "erros_total": sum(n for c, n in codigos.items() if c != 201),
        "lat_media_ms": round(sum(lat) / len(lat), 1),
        "lat_p95_ms": round(percentil(lat, 95), 1),
        "lat_max_ms": round(max(lat), 1),
        "requisicoes_acima_de_500ms": len(lentas),
        "janela_lenta_s": [lentas[0][0], lentas[-1][0]] if lentas else None,
        "fases": {nome: resumo_fase(rs) for nome, rs in fases.items()},
        "ultima_resposta_da_replica_antes_de_religar_s": round(ultima_antes, 2) if ultima_antes is not None else None,
        "primeira_resposta_da_replica_religada_s": round(primeira_depois, 2) if primeira_depois is not None else None,
        "tempo_ate_voltar_a_atender_s": round(primeira_depois - t_volta, 2) if primeira_depois is not None else None,
        "overbooking": stats["overbooking"],
        "reservas_no_banco": sum(stats["reservas_por_status"].values()),
    }

    pasta = Path(args.out)
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"falha_api_{args.modo}_{datetime.now():%Y%m%d-%H%M%S}.json"
    arq.write_text(json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resumo, indent=2, ensure_ascii=False))
    print(f"\nsalvo em {arq}")


if __name__ == "__main__":
    asyncio.run(main())
