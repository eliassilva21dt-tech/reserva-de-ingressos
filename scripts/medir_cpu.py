"""Mede quanta CPU cada contêiner da stack gastou DURANTE uma carga.

Em vez de amostrar `docker stats` (que só mostra o instante da amostra), lê o
contador acumulado de CPU do contêiner (cgroup v2: /sys/fs/cgroup/cpu.stat,
campo usage_usec) antes e depois da carga. A diferença é o tempo de CPU
realmente gasto; dividido pela duração da carga dá o uso médio.

Convenção igual à do `docker stats`: 100% = um núcleo inteiro ocupado.
"""
import shutil
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

PROJETO = "ingressos"  # campo "name:" do docker-compose.yml


def disponivel() -> bool:
    return shutil.which("docker") is not None


def _rodar(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=20, check=True
    ).stdout


def listar_containers() -> dict[str, str]:
    """Devolve {nome do contêiner: serviço do compose}."""
    saida = _rodar(
        "ps",
        "--filter", f"label=com.docker.compose.project={PROJETO}",
        "--format", '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
    )
    return dict(linha.split("\t") for linha in saida.splitlines() if "\t" in linha)


def _usage_usec(nome: str) -> int | None:
    try:
        for linha in _rodar("exec", nome, "cat", "/sys/fs/cgroup/cpu.stat").splitlines():
            if linha.startswith("usage_usec"):
                return int(linha.split()[1])
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return None


def foto(containers: dict[str, str]) -> dict[str, int | None]:
    """Lê o contador de CPU de todos os contêineres (em paralelo)."""
    with ThreadPoolExecutor(max_workers=len(containers) or 1) as pool:
        return dict(zip(containers, pool.map(_usage_usec, containers)))


def nucleos_do_docker() -> int | None:
    try:
        return int(_rodar("info", "--format", "{{.NCPU}}").strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def resumo(containers: dict[str, str], antes: dict, depois: dict, duracao_s: float) -> dict:
    """Uso médio de CPU por serviço durante a carga (% de um núcleo)."""
    por_servico: dict[str, list[float]] = defaultdict(list)
    for nome, servico in containers.items():
        a, d = antes.get(nome), depois.get(nome)
        if a is None or d is None:
            continue
        por_servico[servico].append(round((d - a) / (duracao_s * 1_000_000) * 100, 1))
    return {
        servico: {"soma_pct": round(sum(v), 1), "por_container_pct": sorted(v, reverse=True)}
        for servico, v in sorted(por_servico.items())
    }
