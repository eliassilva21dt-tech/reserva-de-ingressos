"""Gera as tabelas a partir dos JSON em evidencias/ (média ± desvio padrão).

Uso:
    python scripts/resumir.py exp1
    python scripts/resumir.py exp2

Lê só os arquivos da pasta principal (evidencias/exp1, evidencias/exp2); as
medições antigas guardadas em subpastas (ex.: v1/) ficam de fora.
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


def mdp(xs) -> str:
    """'média ± desvio padrão' (desvio amostral; '-' se não houver dados)."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    dp = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return f"{statistics.mean(xs):.1f} ± {dp:.1f}"


def faixa(xs) -> str:
    return f"{min(xs):g} a {max(xs):g}"


def exp1() -> None:
    grupos = defaultdict(list)
    for d in carregar("exp1"):
        grupos[d["modo"]].append(d)
    print("| Modo | Reps | 201 (aceitas) | 409 | Erros | Lat. média (ms) | Lat. p95 (ms) | Tempo na API p/ recusar (ms) | Tempo na API p/ aceitar (ms) | Overbooking? |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for modo in ("none", "db", "redis"):
        ds = grupos.get(modo)
        if not ds:
            continue
        aceitas = [d["aceitas_201"] for d in ds]
        over = f"{sum(d['houve_overbooking'] for d in ds)}/{len(ds)} rodadas"
        print(
            f"| {modo} | {len(ds)} | {media(aceitas)} ({faixa(aceitas)}) | "
            f"{media([d['conflito_409'] for d in ds])} | {media([d['erros'] for d in ds])} | "
            f"{mdp([d['lat_media_ms'] for d in ds])} | {mdp([d['lat_p95_ms'] for d in ds])} | "
            f"{mdp([d.get('api_media_409_ms') for d in ds])} | {mdp([d.get('api_media_201_ms') for d in ds])} | {over} |"
        )
    print("\nLatência média por rodada (ms), na ordem em que rodaram:")
    for modo in ("none", "db", "redis"):
        if grupos.get(modo):
            print(f"  {modo:5}: " + ", ".join(f"{d['lat_media_ms']:g}" for d in sorted(grupos[modo], key=lambda d: d["timestamp"])))


def exp2() -> None:
    # Uma tabela por tipo de carga (leve = a do enunciado; forte = vários processos).
    cargas = defaultdict(lambda: defaultdict(list))
    for d in carregar("exp2"):
        chave = (d.get("processos_gerador", 1), d["requisicoes"], d["concorrencia"])
        cargas[chave][d["replicas"]].append(d)
    for (procs, reqs, conc), grupos in sorted(cargas.items()):
        print()
        print(f"## Carga: {reqs} reservas, {conc} conexões simultâneas, {procs} processo(s) no gerador")
        print()
        tabelas_exp2({n: sorted(ds, key=lambda d: d["timestamp"]) for n, ds in grupos.items()})


def tabelas_exp2(grupos: dict[int, list[dict]]) -> None:
    print("| Réplicas | Reps | Req/s | Lat. média (ms) | Lat. p95 (ms) | Distribuição por instância (última rep.) |")
    print("|---|---|---|---|---|---|")
    for n in sorted(grupos):
        ds = grupos[n]
        dist_txt = " / ".join(str(v) for v in sorted(ds[-1]["distribuicao_por_instancia"].values(), reverse=True))
        print(
            f"| {n} | {len(ds)} | {mdp([d['req_por_s'] for d in ds])} | "
            f"{mdp([d['lat_media_ms'] for d in ds])} | {mdp([d['lat_p95_ms'] for d in ds])} | {dist_txt} |"
        )

    print()
    print("Req/s por rodada, na ordem em que rodaram:")
    for n in sorted(grupos):
        print(f"  {n} réplica(s): " + ", ".join(f"{d['req_por_s']:g}" for d in grupos[n]))

    print()
    print("Onde o tempo de uma requisição é gasto (ms, média ± desvio entre rodadas):")
    print("| Réplicas | Lock (Redis) | Banco (INSERT) | Fila (publicar) | Total na API | Fora da API | Latência vista pelo cliente |")
    print("|---|---|---|---|---|---|---|")
    for n in sorted(grupos):
        ds = grupos[n]
        e = lambda chave: mdp([d["etapas_api_ms"].get(chave) for d in ds])
        print(
            f"| {n} | {e('controle')} | {e('banco')} | {e('fila')} | {e('total')} | "
            f"{mdp([d.get('fora_da_api_ms') for d in ds])} | {mdp([d['lat_media_ms'] for d in ds])} |"
        )

    servicos = sorted({s for ds in grupos.values() for d in ds for s in d["cpu_durante_carga_pct"]})
    nucleos = next((d["nucleos_do_docker"] for ds in grupos.values() for d in ds if d.get("nucleos_do_docker")), "?")
    print()
    print(f"CPU durante a carga (% de um núcleo; nos serviços, soma dos contêineres; Docker com {nucleos} núcleos):")
    print("| Réplicas | api: média por réplica | " + " | ".join(servicos) + " |")
    print("|---|---|" + "---|" * len(servicos))
    for n in sorted(grupos):
        ds = [d for d in grupos[n] if d["cpu_durante_carga_pct"]]
        if not ds:
            continue
        cols = [mdp([d["cpu_durante_carga_pct"].get(s, {}).get("soma_pct") for d in ds]) for s in servicos]
        por_replica = mdp([statistics.mean(d["cpu_durante_carga_pct"]["api"]["por_container_pct"]) for d in ds])
        print(f"| {n} | {por_replica} | " + " | ".join(cols) + " |")


if __name__ == "__main__":
    {"exp1": exp1, "exp2": exp2}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
