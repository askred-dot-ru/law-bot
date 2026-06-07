#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="/tmp/law-bot-deploy"

rm -rf "$DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"

cp "$SCRIPT_DIR/bot.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/mcp-server.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/law_search.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/ingest.py" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/system-prompt.md" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/requirements.txt" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/Dockerfile" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/entrypoint.sh" "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/output.md" "$DEPLOY_DIR/"

export PATH="$HOME/.npm-global/bin:$PATH"
export RAILWAY_API_TOKEN="a5bea384-f730-4ce1-8eee-94ab05204338"

cd "$DEPLOY_DIR" && railway up --detach --service law-bot
