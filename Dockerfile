FROM node:22-bookworm-slim

ARG YTDLP_VERSION=2026.8.19

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates python3 python3-pip \
 && pip3 install --no-cache-dir --no-compile --break-system-packages "yt-dlp[default]==${YTDLP_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY probe.mjs ./
ENV NODE_ENV=production PORT=3000 PYTHONDONTWRITEBYTECODE=1
USER node
EXPOSE 3000
CMD ["node","probe.mjs"]
