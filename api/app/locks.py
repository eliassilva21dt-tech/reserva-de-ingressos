import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from . import config

log = logging.getLogger("api.locks")

# "Apagar só se o valor for meu": evita liberar o lock de outra requisição
# caso o nosso tenha expirado e outro cliente já tenha assumido o assento.
LUA_LIBERAR = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


def chave(assento_id: int) -> str:
    return f"lock:assento:{assento_id}"


def criar_cliente() -> aioredis.Redis:
    return aioredis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
    )


async def adquirir(r: aioredis.Redis, assento_id: int, token: str) -> bool:
    """SET lock:assento:{id} <token> NX EX <ttl>  -> True se conseguimos o lock."""
    return bool(await r.set(chave(assento_id), token, nx=True, ex=config.LOCK_TTL_SECONDS))


async def liberar(r: aioredis.Redis, assento_id: int, token: str) -> None:
    try:
        await r.eval(LUA_LIBERAR, 1, chave(assento_id), token)
    except RedisError as e:
        log.warning("falha ao liberar lock do assento %s (expira pelo TTL): %s", assento_id, e)


async def limpar_todos(r: aioredis.Redis) -> int:
    n = 0
    async for k in r.scan_iter(match="lock:*"):
        n += await r.delete(k)
    return n
