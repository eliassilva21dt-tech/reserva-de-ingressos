import asyncio
import json
import logging

import aio_pika

from . import config

log = logging.getLogger("api.fila")


class Fila:
    """Publicador para a fila durável 'reservas.pagamento'."""

    def __init__(self) -> None:
        self.conexao: aio_pika.abc.AbstractRobustConnection | None = None
        self.canal: aio_pika.abc.AbstractChannel | None = None

    async def conectar(self, tentativas: int = 30) -> None:
        for i in range(1, tentativas + 1):
            try:
                # connect_robust reconecta sozinho se o RabbitMQ cair depois.
                self.conexao = await aio_pika.connect_robust(config.RABBITMQ_URL)
                self.canal = await self.conexao.channel()
                await self.canal.declare_queue(config.NOME_FILA, durable=True)
                log.info("conectado ao RabbitMQ")
                return
            except Exception as e:
                espera = min(i, 5)
                log.warning("rabbitmq indisponível (%s); nova tentativa em %ss", e, espera)
                await asyncio.sleep(espera)
        raise RuntimeError("não foi possível conectar ao RabbitMQ")

    async def publicar(self, reserva_id: int, assento_id: int) -> None:
        corpo = json.dumps({"reserva_id": reserva_id, "assento_id": assento_id}).encode()
        msg = aio_pika.Message(
            body=corpo,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            type="reserva.criada",
        )
        # O timeout evita que a requisição fique presa se o broker estiver fora.
        await asyncio.wait_for(
            self.canal.default_exchange.publish(msg, routing_key=config.NOME_FILA),
            timeout=5,
        )

    async def purgar(self) -> None:
        fila = await self.canal.declare_queue(config.NOME_FILA, durable=True)
        await fila.purge()

    async def fechar(self) -> None:
        if self.conexao:
            await self.conexao.close()
