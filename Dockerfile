# Secrets are not baked in (.dockerignore drops .env). Run with:
#   docker run --env-file .env -v flackey-data:/data -v /path/to/library:/library -p 8765:8765 flackey
# and log in once beforehand with `docker run --env-file .env -it -v flackey-data:/data flackey uv run crate login`.
FROM node:22-slim AS ui
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY --from=ui /web/dist ./web/dist
RUN uv sync --frozen --no-dev
RUN groupadd -r flackey && useradd -r -g flackey -d /app flackey \
    && mkdir -p /data /library \
    && chown -R flackey:flackey /app /data /library
USER flackey
ENV DATA_DIR=/data LIBRARY_ROOT=/library WEB_PORT=8765 WEB_HOST=0.0.0.0
VOLUME ["/data", "/library"]
EXPOSE 8765
CMD ["uv", "run", "crate", "start", "--no-browser"]
