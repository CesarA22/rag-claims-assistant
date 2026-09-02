CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
  id text PRIMARY KEY,
  document_code text NOT NULL,
  document_title text NOT NULL,
  section text NOT NULL,
  version text NOT NULL,
  effective_date date NOT NULL,
  product text NOT NULL,
  doc_role text NOT NULL,
  chunk_kind text NOT NULL,
  text text NOT NULL,
  superseded boolean NOT NULL DEFAULT false,
  contains_pii boolean NOT NULL DEFAULT false,
  pii_kinds text[] NOT NULL DEFAULT '{}',
  caption text,
  footnotes text[] NOT NULL DEFAULT '{}',
  page_from int NOT NULL,
  page_to int NOT NULL,
  embedding vector(1536),
  tsv tsvector GENERATED ALWAYS AS
      (to_tsvector('portuguese', coalesce(section, '') || ' ' || text)) STORED
);

CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_filters ON chunks (superseded, product);
-- no index on embedding: ~200 rows, exact scan beats HNSW. Revisit near 50k.
