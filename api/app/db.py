import asyncio
import logging

import asyncpg

from . import config

log = logging.getLogger("api.db")

ATIVAS = "status IN ('PENDENTE','CONFIRMADA')"

SQL_CRIAR_INDICE = f"""
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_ativa
ON reservas (assento_id)
WHERE {ATIVAS}
"""

SQL_OVERBOOKING = f"""
SELECT assento_id, count(*) AS reservas_ativas
FROM reservas
WHERE {ATIVAS}
GROUP BY assento_id
HAVING count(*) > 1
"""


async def criar_pool(tentativas: int = 30) -> asyncpg.Pool:
    """Conecta ao Postgres, tentando de novo se ele ainda estiver subindo."""
    for i in range(1, tentativas + 1):
        try:
            return await asyncpg.create_pool(
                config.DATABASE_URL, min_size=2, max_size=config.DB_POOL_MAX
            )
        except (OSError, asyncpg.PostgresError) as e:
            espera = min(i, 5)
            log.warning("postgres indisponível (%s); nova tentativa em %ss", e, espera)
            await asyncio.sleep(espera)
    raise RuntimeError("não foi possível conectar ao Postgres")


async def ajustar_indice(pool: asyncpg.Pool) -> None:
    """Cria o índice único nos modos db/redis e remove no modo none.

    Duas réplicas podem executar isto ao mesmo tempo, por isso o try/except.
    Se já existirem duplicatas (sobra de um experimento em modo none), o
    CREATE falha: nesse caso rode POST /admin/reset, que chama esta função de novo.
    """
    try:
        async with pool.acquire() as c:
            if config.MODO_LOCK == "none":
                await c.execute("DROP INDEX IF EXISTS uq_reserva_ativa")
                log.info("modo=none: índice único removido")
            else:
                await c.execute(SQL_CRIAR_INDICE)
                log.info("modo=%s: índice único garantido", config.MODO_LOCK)
    except Exception as e:  # corrida entre réplicas ou duplicatas existentes
        log.warning("não foi possível ajustar o índice único: %s", e)


async def assento_existe(c: asyncpg.Connection, assento_id: int) -> bool:
    return await c.fetchval("SELECT EXISTS(SELECT 1 FROM assentos WHERE id = $1)", assento_id)


async def assento_ocupado(c: asyncpg.Connection, assento_id: int) -> bool:
    return await c.fetchval(
        f"SELECT EXISTS(SELECT 1 FROM reservas WHERE assento_id = $1 AND {ATIVAS})",
        assento_id,
    )


async def inserir_reserva(pool: asyncpg.Pool, assento_id: int, cliente: str) -> int:
    async with pool.acquire() as c:
        return await c.fetchval(
            "INSERT INTO reservas (assento_id, cliente) VALUES ($1, $2) RETURNING id",
            assento_id,
            cliente,
        )


async def apagar_reserva(pool: asyncpg.Pool, reserva_id: int) -> None:
    async with pool.acquire() as c:
        await c.execute("DELETE FROM reservas WHERE id = $1", reserva_id)
