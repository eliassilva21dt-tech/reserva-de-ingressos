import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from . import config, db, locks
from .fila import Fila

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s %(levelname)s [{config.HOSTNAME}] %(name)s: %(message)s",
)
log = logging.getLogger("api")


class Estado:
    pool: asyncpg.Pool
    redis = None
    fila: Fila


estado = Estado()


@asynccontextmanager
async def lifespan(app: FastAPI):
    estado.pool = await db.criar_pool()
    estado.redis = locks.criar_cliente()
    estado.fila = Fila()
    await estado.fila.conectar()
    await db.ajustar_indice(estado.pool)
    log.info("API pronta (MODO_LOCK=%s)", config.MODO_LOCK)
    yield
    await estado.fila.fechar()
    await estado.redis.aclose()
    await estado.pool.close()


app = FastAPI(title="Reserva de Ingressos", lifespan=lifespan)


class NovaReserva(BaseModel):
    assento_id: int
    cliente: str = Field(min_length=1)


def indisponivel() -> JSONResponse:
    return JSONResponse({"erro": "assento indisponível"}, status_code=409)


def servico_fora(motivo: str) -> JSONResponse:
    return JSONResponse({"erro": f"dependência indisponível: {motivo}"}, status_code=503)


# ----------------------------------------------------------------------------
# Rotas
# ----------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok", "instancia": config.HOSTNAME}


@app.get("/eventos/{evento_id}/assentos")
async def listar_assentos(evento_id: int):
    async with estado.pool.acquire() as c:
        existe = await c.fetchval("SELECT EXISTS(SELECT 1 FROM eventos WHERE id = $1)", evento_id)
        if not existe:
            raise HTTPException(404, "evento inexistente")
        linhas = await c.fetch(
            f"""
            SELECT a.id, a.codigo,
                   EXISTS(SELECT 1 FROM reservas r
                          WHERE r.assento_id = a.id AND r.{db.ATIVAS}) AS ocupado
            FROM assentos a
            WHERE a.evento_id = $1
            ORDER BY a.id
            """,
            evento_id,
        )
    return [
        {"id": r["id"], "codigo": r["codigo"], "status": "reservado" if r["ocupado"] else "livre"}
        for r in linhas
    ]


@app.post("/reservas")
async def criar_reserva(corpo: NovaReserva):
    assento_id, cliente = corpo.assento_id, corpo.cliente
    modo = config.MODO_LOCK
    token = None

    try:
        # ---- 1. Controle de concorrência, conforme o modo ----------------
        if modo == "none":
            # Verifica -> espera -> insere. SEM proteção: de propósito.
            async with estado.pool.acquire() as c:
                if not await db.assento_existe(c, assento_id):
                    return JSONResponse({"erro": "assento inexistente"}, status_code=404)
                ocupado = await db.assento_ocupado(c, assento_id)
            if ocupado:
                log.info("assento ocupado assento=%s cliente=%s", assento_id, cliente)
                return indisponivel()
            # Amplia a janela da condição de corrida (só para o experimento).
            await asyncio.sleep(config.CHECK_INSERT_DELAY_MS / 1000)

        elif modo == "redis":
            token = uuid.uuid4().hex
            if not await locks.adquirir(estado.redis, assento_id, token):
                log.info("lock negado assento=%s cliente=%s", assento_id, cliente)
                return indisponivel()

        # ---- 2. Grava a reserva como PENDENTE -----------------------------
        try:
            reserva_id = await db.inserir_reserva(estado.pool, assento_id, cliente)
        except asyncpg.UniqueViolationError:
            # modo db (barreira única) ou redis (segunda barreira)
            log.info("violação do índice único assento=%s cliente=%s", assento_id, cliente)
            if token:
                await locks.liberar(estado.redis, assento_id, token)
            return indisponivel()
        except asyncpg.ForeignKeyViolationError:
            if token:
                await locks.liberar(estado.redis, assento_id, token)
            return JSONResponse({"erro": "assento inexistente"}, status_code=404)
        except Exception:
            if token:
                await locks.liberar(estado.redis, assento_id, token)
            raise

        # ---- 3. Publica na fila para o pagamento --------------------------
        try:
            await estado.fila.publicar(reserva_id, assento_id)
        except Exception as e:
            # Sem fila, ninguém vai processar o pagamento: desfaz a reserva.
            log.error("falha ao publicar reserva=%s: %s (reserva desfeita)", reserva_id, e)
            await db.apagar_reserva(estado.pool, reserva_id)
            if token:
                await locks.liberar(estado.redis, assento_id, token)
            return servico_fora("RabbitMQ")

    except RedisError as e:
        log.error("Redis indisponível: %s", e)
        return servico_fora("Redis")
    except (asyncpg.PostgresError, OSError) as e:
        log.error("Postgres indisponível: %s", e)
        return servico_fora("Postgres")

    log.info(
        "reserva criada id=%s assento=%s cliente=%s modo=%s",
        reserva_id, assento_id, cliente, modo,
    )
    return JSONResponse(
        {
            "id": reserva_id,
            "assento_id": assento_id,
            "cliente": cliente,
            "status": "PENDENTE",
            "instancia": config.HOSTNAME,
        },
        status_code=201,
    )


@app.get("/reservas/{reserva_id}")
async def obter_reserva(reserva_id: int):
    async with estado.pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT id, assento_id, cliente, status, criado_em, atualizado_em "
            "FROM reservas WHERE id = $1",
            reserva_id,
        )
    if r is None:
        raise HTTPException(404, "reserva inexistente")
    return {
        "id": r["id"],
        "assento_id": r["assento_id"],
        "cliente": r["cliente"],
        "status": r["status"],
        "criado_em": r["criado_em"].isoformat(),
        "atualizado_em": r["atualizado_em"].isoformat(),
        "instancia": config.HOSTNAME,
    }


# ----------------------------------------------------------------------------
# Admin (somente desenvolvimento)
# ----------------------------------------------------------------------------
def exigir_admin() -> None:
    if not config.ENABLE_ADMIN:
        raise HTTPException(404, "not found")


@app.post("/admin/reset")
async def admin_reset():
    exigir_admin()
    try:
        async with estado.pool.acquire() as c:
            await c.execute("TRUNCATE reservas RESTART IDENTITY")
        locks_apagados = await locks.limpar_todos(estado.redis)
        await estado.fila.purgar()
    except RedisError as e:
        return servico_fora(f"Redis ({e})")
    except Exception as e:
        return servico_fora(str(e))
    await db.ajustar_indice(estado.pool)  # recria o índice se uma rodada anterior impediu
    log.info("reset: reservas apagadas, %s locks removidos, fila limpa", locks_apagados)
    return {"status": "ok", "locks_removidos": locks_apagados, "modo": config.MODO_LOCK}


@app.get("/admin/stats")
async def admin_stats():
    exigir_admin()
    async with estado.pool.acquire() as c:
        por_status = await c.fetch("SELECT status, count(*) AS n FROM reservas GROUP BY status")
        dup = await c.fetch(db.SQL_OVERBOOKING)
    return {
        "modo": config.MODO_LOCK,
        "instancia": config.HOSTNAME,
        "reservas_por_status": {r["status"]: r["n"] for r in por_status},
        "overbooking": [
            {"assento_id": r["assento_id"], "reservas_ativas": r["reservas_ativas"]} for r in dup
        ],
    }
