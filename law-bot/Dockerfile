# Stage 1: builder — indexing vectors
FROM python:3.11-slim AS builder
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY output.md ingest.py ./
RUN python ingest.py

# Stage 2: runtime — MCP + bot
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /app/db/ /app/db/
COPY --from=builder /root/.cache/huggingface/ /root/.cache/huggingface/

COPY mcp-server.py bot.py law_search.py system-prompt.md entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8080 8090
CMD ["./entrypoint.sh"]
