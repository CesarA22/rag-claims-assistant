#!/bin/sh
# Boot the API container: migrate, index if the index is empty, serve.
#
# Idempotent on purpose. A fresh volume has an empty database, and an empty
# database means retrieval returns nothing and the sufficiency gate correctly
# refuses every question — an app that looks broken while being correct. So the
# first boot ingests. Every boot after that finds rows and skips straight to
# serving, instead of re-parsing thirteen PDFs on every `docker compose up`.
set -eu

echo "==> alembic upgrade head"
alembic upgrade head

# Not inlined into `if [ "$(...)" = "0" ]`: a failure inside a condition is not
# an error to `set -e`, so a crashing count would print an empty string, compare
# false, skip the ingest and serve an empty index. As an assignment it aborts.
count=$(python -m scripts.chunk_count)

if [ "$count" = "0" ]; then
  # --no-embed stores NULL vectors. There is no embeddings cache in the
  # repository and EmbeddingCache refuses to invent one, so a keyless clone
  # cannot build the vector arm; RETRIEVER_ARM=lexical never reads it, and S3
  # measured lexical recall@5 at 8/9 — the same as hybrid on this corpus.
  echo "==> empty index; ingesting data/corpus (this takes a minute)"
  python -m app.retrieval.ingest data/corpus --no-embed
else
  echo "==> index already holds $count chunks; skipping ingest"
fi

echo "==> serving on 0.0.0.0:8000"
exec uvicorn --factory app.main:create_container_app --host 0.0.0.0 --port 8000
