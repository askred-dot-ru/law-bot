#!/usr/bin/env python3
import os, sys, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastmcp import FastMCP
import law_search

PORT = int(os.environ.get("MCP_PORT", "8090"))

mcp = FastMCP("LawBot MCP Server")


@mcp.tool()
def search_law(query: str, top_k: int = 5) -> str:
    results = law_search.search_law(query, top_k=top_k)
    if not results:
        return "Ничего не найдено по вашему запросу."
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
def get_article(codex: str, article: str) -> str:
    result = law_search.get_article(codex, article)
    if result is None:
        return f"Статья {article} не найдена в кодексе '{codex}'."
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def list_codexes() -> str:
    results = law_search.list_codexes()
    return json.dumps(results, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=PORT)
