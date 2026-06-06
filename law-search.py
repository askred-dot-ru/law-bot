import os, re, json
from sentence_transformers import SentenceTransformer
import chromadb

CHROMA_PATH = os.environ.get("CHROMA_PATH", "/app/db/chroma")
COLLECTION_NAME = "law_codes"
MODEL_NAME = "intfloat/multilingual-e5-small"

CODEX_ALIASES = {
    "гк": "Гражданский кодекс РФ",
    "гк рф": "Гражданский кодекс РФ",
    "гражданский": "Гражданский кодекс РФ",
    "ук": "Уголовный кодекс РФ",
    "ук рф": "Уголовный кодекс РФ",
    "уголовный": "Уголовный кодекс РФ",
    "ск": "Семейный кодекс РФ",
    "ск рф": "Семейный кодекс РФ",
    "семейный": "Семейный кодекс РФ",
    "нк": "Налоговый кодекс РФ",
    "нк рф": "Налоговый кодекс РФ",
    "налоговый": "Налоговый кодекс РФ",
}

_model = None
_collection = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = client.get_collection(COLLECTION_NAME)
    return _collection


def _resolve_codex(name: str) -> str:
    name_lower = name.strip().lower()
    if name_lower in CODEX_ALIASES:
        return CODEX_ALIASES[name_lower]
    for key, val in CODEX_ALIASES.items():
        if key in name_lower:
            return val
    return name.strip()


def _clean_metadata(meta: dict) -> dict:
    return {k: str(v) for k, v in (meta or {}).items()}


def search_law(query: str, top_k: int = 5) -> list[dict]:
    model = _get_model()
    collection = _get_collection()

    embedding = model.encode(f"query: {query}").tolist()
    results = collection.query(query_embeddings=[embedding], n_results=top_k)

    items = []
    if results["ids"] and results["ids"][0]:
        for i in range(len(results["ids"][0])):
            meta = _clean_metadata(results["metadatas"][0][i])
            items.append({
                "codex": meta.get("codex", ""),
                "article": meta.get("article", ""),
                "section": meta.get("section", ""),
                "text": results["documents"][0][i],
            })
    return items


def get_article(codex: str, article: str) -> dict | None:
    codex_resolved = _resolve_codex(codex)
    collection = _get_collection()

    results = collection.get(
        where={"$and": [
            {"codex": codex_resolved},
            {"article": article},
        ]}
    )

    if not results["ids"]:
        return None

    texts = []
    for i, doc in enumerate(results["documents"]):
        meta = _clean_metadata(results["metadatas"][i])
        texts.append(f"[Чанк {meta.get('chunk_index', i)}] {doc}")

    return {
        "codex": results["metadatas"][0].get("codex", "") if results["metadatas"] else "",
        "article": results["metadatas"][0].get("article", "") if results["metadatas"] else "",
        "text": "\n\n".join(texts),
    }


def list_codexes() -> list[dict]:
    collection = _get_collection()
    results = collection.get(include=["metadatas"])

    codex_counts = {}
    codex_sections = {}
    for meta in results["metadatas"]:
        c = meta.get("codex", "Неизвестно")
        if c not in codex_counts:
            codex_counts[c] = set()
            codex_sections[c] = set()
        codex_counts[c].add(meta.get("article", ""))
        codex_sections[c].add(meta.get("section", ""))

    return [
        {
            "codex": name,
            "article_count": len(articles),
        }
        for name, articles in sorted(codex_counts.items())
    ]
