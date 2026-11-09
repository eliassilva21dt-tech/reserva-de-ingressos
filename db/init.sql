-- Executado automaticamente na primeira subida do contêiner (volume pgdata vazio).

CREATE TABLE eventos (
  id SERIAL PRIMARY KEY,
  nome TEXT NOT NULL
);

CREATE TABLE assentos (
  id SERIAL PRIMARY KEY,
  evento_id INT NOT NULL REFERENCES eventos(id),
  codigo TEXT NOT NULL,
  UNIQUE (evento_id, codigo)
);

CREATE TABLE reservas (
  id SERIAL PRIMARY KEY,
  assento_id INT NOT NULL REFERENCES assentos(id),
  cliente TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDENTE'
    CHECK (status IN ('PENDENTE','CONFIRMADA','RECUSADA')),
  criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
  atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice comum (não único) para acelerar "este assento tem reserva ativa?".
-- Existe em todos os modos, inclusive no modo "none" (sem índice ÚNICO).
CREATE INDEX idx_reservas_assento ON reservas (assento_id);

-- Seed: 1 evento com 1000 assentos (A1..A1000) para os experimentos
INSERT INTO eventos (nome) VALUES ('Show de Teste');
INSERT INTO assentos (evento_id, codigo)
SELECT 1, 'A' || g FROM generate_series(1, 1000) AS g;

-- O índice ÚNICO parcial "uq_reserva_ativa" é criado/removido pela API
-- ao iniciar, conforme MODO_LOCK (ver PROJETO.md, seção 4).
