@echo off
node -e "if(Number(process.versions.node.split('.')[0])<22){console.error('Node.js 22+ is required.');process.exit(1)}" || exit /b 1
if "%HOST%"=="" set HOST=127.0.0.1
if "%PORT%"=="" set PORT=3000
echo Open http://127.0.0.1:%PORT% in your browser.
node server.js
