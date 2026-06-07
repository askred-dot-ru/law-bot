#!/bin/bash
set -e

echo "=== LawBot starting ==="

echo "[1/2] Starting MCP server on port ${MCP_PORT:-8090} ..."
python mcp-server.py &
MCP_PID=$!
sleep 3

echo "[2/2] Starting Telegram bot on port ${PORT:-8080} ..."
python bot.py

kill $MCP_PID 2>/dev/null
