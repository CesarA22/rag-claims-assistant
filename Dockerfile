# Multi-stage so Node never ships. The client is built here and the runtime
# image holds a directory of static files.
FROM node:20-slim AS client
WORKDIR /web
# The lockfile alone, so `npm ci` is cached until a dependency actually moves.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# requires-python = ">=3.12", so 3.12-slim. pymupdf wheels lag new minors and
# 3.13 is untested here.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# No apt layer. The plan expected pymupdf to need libgl1 and libglib2.0-0; it
# does not — those are opencv's, and pymupdf 1.28 wheels are self-contained.
# Verified by running the real ingest over the thirteen PDFs in this image, not
# by importing the module.
COPY pyproject.toml ./
COPY app/ ./app/
RUN pip install --no-cache-dir -e .

COPY alembic.ini ./
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY --from=client /web/dist ./web/dist
COPY docker/entrypoint.sh /usr/local/bin/entrypoint
# Belt and braces on top of .gitattributes: this repository is developed on
# Windows with core.autocrlf=true, and a CRLF `#!/bin/sh` fails in a Linux
# image with "no such file or directory" — which reads like a missing file.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint && chmod +x /usr/local/bin/entrypoint

# Nothing here writes to disk at runtime: the index goes to Postgres and
# --no-embed never opens the embeddings cache. So the server does not need to
# own its own source tree.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
USER appuser

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint"]
