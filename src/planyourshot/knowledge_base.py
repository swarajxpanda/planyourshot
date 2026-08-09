from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_chroma import Chroma
from langchain_ollama import OllamaEmbeddings

from planyourshot.config import EMBED_MODEL, CHROMA_DIR, KNOWLEDGE_DIR

# Split on ## headers; keep the header text as metadata on each chunk.
_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "doc_title"), ("##", "section")],
    strip_headers=False,
)


def _embeddings() -> OllamaEmbeddings:
    """One place that builds the embedding model, mirroring get_llm()."""
    return OllamaEmbeddings(model=EMBED_MODEL)


def load_and_chunk(knowledge_dir: str = KNOWLEDGE_DIR) -> list[Document]:
    """Load every .md doc and split it into per-section chunks."""
    chunks: list[Document] = []
    for md_path in sorted(Path(knowledge_dir).rglob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        # category = the subfolder (cinematography / shot_types / references)
        category = md_path.parent.name
        for chunk in _splitter.split_text(text):
            chunk.metadata["source"] = str(md_path)
            chunk.metadata["category"] = category
            chunks.append(chunk)
    return chunks


def build_index(knowledge_dir: str = KNOWLEDGE_DIR, persist_dir: str = CHROMA_DIR) -> Chroma:
    """Embed all chunks and persist them to a local Chroma store."""
    chunks = load_and_chunk(knowledge_dir)
    store = Chroma.from_documents(
        documents=chunks,
        embedding=_embeddings(),
        persist_directory=persist_dir,
        collection_name="cinematography",
    )
    print(f"Indexed {len(chunks)} chunks from {knowledge_dir} → {persist_dir}")
    return store


def get_store(persist_dir: str = CHROMA_DIR) -> Chroma:
    """Open the existing store (no re-embedding)."""
    return Chroma(
        embedding_function=_embeddings(),
        persist_directory=persist_dir,
        collection_name="cinematography",
    )


def search(query: str, k: int = 4, persist_dir: str = CHROMA_DIR) -> list[Document]:
    """Semantic search — returns the k most relevant chunks."""
    return get_store(persist_dir).similarity_search(query, k=k)


if __name__ == "__main__":
    import sys

    # No args → build the index. With args → treat them as a search query.
    if len(sys.argv) == 1:
        build_index()
    else:
        query = " ".join(sys.argv[1:])
        print(f"\nQuery: {query}\n" + "=" * 60)
        for i, doc in enumerate(search(query), 1):
            section = doc.metadata.get("section", "?")
            src = Path(doc.metadata.get("source", "?")).name
            print(f"\n[{i}] {src} → {section}")
            print(doc.page_content.strip()[:400])