#!/usr/bin/env sh
set -eu
node -e "if(Number(process.versions.node.split('.')[0])<22){console.error('Node.js 22+ is required.');process.exit(1)}"
HOST="${HOST:-127.0.0.1}" PORT="${PORT:-3000}" exec node server.js
