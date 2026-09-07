FROM node:22-bookworm-slim

# Pin the extractor for reproducible builds. Override at build time when YouTube
# changes: docker build --build-arg YTDLP_VERSION=<new stable> .
ARG YTDLP_VERSION=2026.8.19

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates ffmpeg python3 python3-pip \
 && pip3 install --no-cache-dir --no-compile --break-system-packages "yt-dlp[default]==${YTDLP_VERSION}" \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN mkdir -p /data && chown node:node /data
COPY --chown=node:node package.json package-lock.json server.js ./
COPY --chown=node:node lib ./lib
COPY --chown=node:node public ./public

ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000 PYTHONDONTWRITEBYTECODE=1 MONITOR_DATA_DIR=/data/monitoring
VOLUME ["/data"]
USER node
EXPOSE 3000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["node","-e","fetch('http://127.0.0.1:3000/api/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["node","server.js"]
