"""pagamento-service: consome 'reservas.pagamento', simula o pagamento e atualiza o status.

Garantias:
- ack MANUAL e só depois do UPDATE no banco: se o processo morrer no meio,
  o RabbitMQ reentrega a mensagem;
- prefetch=1: uma mensagem por vez;
- idempotência: UPDATE só atua se o status ainda for PENDENTE, então uma
  reentrega não gera efeito duplicado.
"""
import asyncio
import json
import logging
import os
import random
import socket

import aio_pika
import asyncpg
import redis.asyncio as aioredis

HOSTNAME = socket.gethostname()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@db:5432/ingressos")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
DELAY_MIN = float(os.getenv("PAGAMENTO_DELAY_MIN", "1"))
DELAY_MAX = float(os.getenv("PAGAMENTO_DELAY_MAX", "3"))
TAXA_RECUSA = float(os.getenv("PAGAMENTO_TAXA_RECUSA", "0.1"))
NOME_FILA = "reservas.pagamento"

logging.basicConfig(
    level=logging.INFO,
    format=f"%(asctime)s %(levelname)s [{HOSTNAME}] pagamento: %(message)s",
)
log = logging.getLogger("pagamento")


async def com_retry(descricao, funcao, tentativas: int = 30):
    for i in range(1, tentativas + 1):
        try:
            return await funcao()
        except Exception as e:
            espera = min(i, 5)
            log.warning("%s indisponível (%s); nova tentativa em %ss", descricao, e, espera)
            await asyncio.sleep(espera)
    raise RuntimeError(f"não foi possível conectar: {descricao}")


async def processar(mensagem: aio_pika.abc.AbstractIncomingMessage, pool, redis) -> None:
    try:
        dados = json.loads(mensagem.body)
        reserva_id = int(dados["reserva_id"])
        assento_id = int(dados["assento_id"])
    except (ValueError, KeyError, TypeError):
        log.error("mensagem inválida descartada: %r", mensagem.body)
        await mensagem.reject(requeue=False)
        return

    redelivered = " (REENTREGA)" if mensagem.redelivered else ""
    log.info("processando reserva=%s assento=%s%s", reserva_id, assento_id, redelivered)

    try:
        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))  # "gateway de pagamento"
        novo_status = "RECUSADA" if random.random() < TAXA_RECUSA else "CONFIRMADA"

        resultado = await pool.execute(
            "UPDATE reservas SET status = $2, atualizado_em = now() "
            "WHERE id = $1 AND status = 'PENDENTE'",
            reserva_id,
            novo_status,
        )
        if resultado == "UPDATE 1":
            log.info("pagamento %s reserva=%s assento=%s", novo_status.lower(), reserva_id, assento_id)
            if novo_status == "RECUSADA":
                # Assento volta a ficar livre: remove o lock do Redis (se existir).
                try:
                    await redis.delete(f"lock:assento:{assento_id}")
                    log.info("lock liberado assento=%s", assento_id)
                except Exception as e:
                    log.warning("não foi possível liberar o lock (expira pelo TTL): %s", e)
        else:
            log.info("reserva=%s já processada ou inexistente; ignorando (idempotência)", reserva_id)

        await mensagem.ack()  # só agora: o banco já foi atualizado
    except Exception as e:
        log.exception("erro ao processar reserva=%s: %s (devolvendo à fila)", reserva_id, e)
        await asyncio.sleep(1)
        await mensagem.nack(requeue=True)


async def main() -> None:
    pool = await com_retry("postgres", lambda: asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3))
    redis = aioredis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2, socket_connect_timeout=2)
    conexao = await com_retry("rabbitmq", lambda: aio_pika.connect_robust(RABBITMQ_URL))

    canal = await conexao.channel()
    await canal.set_qos(prefetch_count=1)
    fila = await canal.declare_queue(NOME_FILA, durable=True)

    async def callback(msg):
        await processar(msg, pool, redis)

    await fila.consume(callback)  # no_ack=False por padrão: ack manual
    log.info("aguardando mensagens em %s", NOME_FILA)
    await asyncio.Future()  # roda para sempre


if __name__ == "__main__":
    asyncio.run(main())
