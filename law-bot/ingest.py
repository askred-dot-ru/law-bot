import re, os, sys
from sentence_transformers import SentenceTransformer
import chromadb

INPUT_MD = os.environ.get("INPUT_MD", "output.md")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "/app/db/chroma")
COLLECTION_NAME = "law_codes"
MODEL_NAME = "intfloat/multilingual-e5-small"
CHUNK_SIZE = 400
OVERLAP = 50
BATCH_SIZE = 32

CODEX_NAME_MAP = {
    "gar1ant_grajdansky_kodeks_rf": "Гражданский кодекс РФ (часть 1)",
    "gar2ant_nalogovy_kodeks_rf": "Налоговый кодекс РФ",
    "garant_grajdansky_kodeks_rf": "Гражданский кодекс РФ (часть 2)",
}


def clean_codex(name: str) -> str:
    import re
    return re.sub(r'\s*\(часть\s+\d+\)\s*', '', name).strip()


def normalize_codex_name(raw: str) -> str:
    raw = raw.strip()
    if raw in CODEX_NAME_MAP:
        return CODEX_NAME_MAP[raw]
    if "гражданск" in raw.lower():
        return "Гражданский кодекс РФ"
    if "уголовн" in raw.lower():
        return "Уголовный кодекс РФ"
    if "семейн" in raw.lower():
        return "Семейный кодекс РФ"
    if "налогов" in raw.lower():
        return "Налоговый кодекс РФ"
    return raw


def find_articles(text: str) -> list[tuple[str, str, str]]:
    pattern = re.compile(r'(Статья\s+\d+(?:\.\d+)?(?:\s*\d+)?[.\s])')
    matches = list(pattern.finditer(text))

    articles = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        article_num = m.group(1).strip().rstrip(".")
        article_text = text[start:end].strip()
        articles.append((article_num, article_text))
    return articles


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


def extract_section(text: str, codex: str, article: str) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("Статья") and not line.startswith("Дата"):
            if "Раздел" in line or "Глава" in line or "Подраздел" in line or "Часть" in line:
                return line[:120]
    return ""


def main():
    print(f"Reading {INPUT_MD} ...")
    with open(INPUT_MD, "r", encoding="utf-8") as f:
        content = f.read()

    sections = re.split(r'\n(?=# )', content)
    print(f"Found {len(sections)} top-level sections")

    print(f"Loading model {MODEL_NAME} ...")
    model = SentenceTransformer(MODEL_NAME)

    os.makedirs(CHROMA_PATH, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_PATH)

    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    total_chunks = 0
    all_ids = []
    all_embeddings = []
    all_documents = []
    all_metadatas = []
    seen_ids = set()
    section_idx = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue

        header_match = re.match(r'^# (.+)', section)
        if not header_match:
            continue
        raw_name = header_match.group(1)
        codex = normalize_codex_name(raw_name)
        section_idx += 1
        print(f"\nProcessing: {codex}")

        articles = find_articles(section)
        if not articles:
            codex_section_text = section[header_match.end():].strip()
            articles = [("Общие положения", codex_section_text)]

        for article_num, article_text in articles:
            section_name = extract_section(section, codex, article_num)
            chunks = chunk_text(article_text)

            for ci, chunk in enumerate(chunks):
                codex_clean = clean_codex(codex)
                chunk_id = f"s{section_idx}_{codex.replace(' ', '_')}_{article_num.replace(' ', '_')}_{ci}"
                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)
                meta = {
                    "codex": codex_clean,
                    "article": article_num,
                    "section": section_name,
                    "chunk_index": ci,
                }
                all_ids.append(chunk_id)
                all_documents.append(chunk)
                all_metadatas.append(meta)
                total_chunks += 1

        print(f"  Chunks so far: {total_chunks}")

    print(f"\nTotal chunks: {total_chunks}")
    print("Embedding chunks ...")

    texts = [f"passage: {doc}" for doc in all_documents]

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_ids = all_ids[i:i + BATCH_SIZE]
        batch_docs = all_documents[i:i + BATCH_SIZE]
        batch_meta = all_metadatas[i:i + BATCH_SIZE]

        embeddings = model.encode(batch).tolist()

        collection.add(
            ids=batch_ids,
            embeddings=embeddings,
            documents=batch_docs,
            metadatas=batch_meta,
        )

        pct = min(100, (i + BATCH_SIZE) * 100 // len(texts))
        print(f"  {pct}% ({i + len(batch)}/{len(texts)} chunks)")

    print(f"\nDone. {total_chunks} chunks indexed to {CHROMA_PATH}")


if __name__ == "__main__":
    main()
