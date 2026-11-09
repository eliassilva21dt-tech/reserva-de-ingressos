"""Gera as tabelas (médias das repetições) a partir dos JSON em evidencias/.

Uso:
    python scripts/resumir.py exp1
    python scripts/resumir.py exp2
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def carregar(pasta: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path("evidencias", pasta).glob(f"{pasta}_*.json"))]


def media(xs):
    return round(statistics.mean(xs), 1)


def exp1() -> None:
    grupos = defaultdict(list)
    for d in carregar("exp1"):
        grupos[d["modo"]].append(d)
    print("| Modo | Reps | 201 (aceitas) | 409 | Erros | Lat. média (ms) | Lat. p95 (ms) | Overbooking? |")
    print("|---|---|---|---|---|---|---|---|")
    for modo in ("none", "db", "redis"):
        ds = grupos.get(modo)
        if not ds:
            continue
        over = f"{sum(d['houve_overbooking'] for d in ds)}/{len(ds)} rodadas"
        print(
            f"| {modo} | {len(ds)} | {media([d['aceitas_201'] for d in ds])} | "
            f"{media([d['conflito_409'] for d in ds])} | {media([d['erros'] for d in ds])} | "
            f"{media([d['lat_media_ms'] for d in ds])} | {media([d['lat_p95_ms'] for d in ds])} | {over} |"
        )


def exp2() -> None:
    grupos = defaultdict(list)
    for d in carregar("exp2"):
        grupos[d["replicas"]].append(d)
    print("| Réplicas | Reps | Tempo total (s) | Req/s | Lat. média (ms) | Lat. p95 (ms) | Distribuição por instância |")
    print("|---|---|---|---|---|---|---|")
    for n in sorted(grupos):
        ds = grupos[n]
        dist = ds[-1]["distribuicao_por_instancia"]
        dist_txt = ", ".join(f"{k[:12]}:{v}" for k, v in dist.items())
        print(
            f"| {n} | {len(ds)} | {media([d['tempo_total_s'] for d in ds])} | "
            f"{media([d['req_por_s'] for d in ds])} | {media([d['lat_media_ms'] for d in ds])} | "
            f"{media([d['lat_p95_ms'] for d in ds])} | {dist_txt} (última rep.) |"
        )


if __name__ == "__main__":
    {"exp1": exp1, "exp2": exp2}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
