import os
import socket

HOSTNAME = socket.gethostname()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@db:5432/ingressos")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

MODO_LOCK = os.getenv("MODO_LOCK", "redis").lower()  # none | db | redis
CHECK_INSERT_DELAY_MS = int(os.getenv("CHECK_INSERT_DELAY_MS", "50"))
LOCK_TTL_SECONDS = int(os.getenv("LOCK_TTL_SECONDS", "30"))
ENABLE_ADMIN = os.getenv("ENABLE_ADMIN", "false").lower() == "true"

DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))

NOME_FILA = "reservas.pagamento"

if MODO_LOCK not in ("none", "db", "redis"):
    raise SystemExit(f"MODO_LOCK inválido: {MODO_LOCK!r} (use none, db ou redis)")
