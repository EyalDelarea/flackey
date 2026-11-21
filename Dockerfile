# Secrets are not baked in (.dockerignore drops .env). Run with:
#   docker run --env-file .env -v flackey-data:/data -v /path/to/library:/library -p 127.0.0.1:8765:8765 flackey
# and log in once beforehand with `docker run --env-file .env -it -v flackey-data:/data flackey uv run flackey login`.
# Base images pinned by digest (multi-arch index); Dependabot's docker entry bumps tag and digest together.
FROM node:26-slim@sha256:e6bedef217677b276c591795e169840edd9096dbf13f29df8ef8d1856b8820bc AS ui
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /usr/local/bin/uv
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
CMD ["uv", "run", "flackey", "start", "--no-browser"]
