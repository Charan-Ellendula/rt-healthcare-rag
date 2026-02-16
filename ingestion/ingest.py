# ingestion/ingest.py
# ingestion/ingest.py
# ============================================================
# Ingestion pipeline for RT Healthcare RAG
# - Loads documents from data/
# - Parent splitter (coherent blocks)
# - Child splitter (smaller chunks for retrieval)
# - Stores parents + children in ChromaDB:
#     collection: rt_parents (full parent blocks)
#     collection: rt_children (child chunks w/ parent_id)
# - Adds metadata for RBAC filtering:
#     department = folder name (e.g., hr, engineering, policies)
# - Supports: .md, .txt, .pdf
#
# Exposes:
#   run_ingestion()  -> callable from Streamlit Cloud
# ============================================================

import os
import re
import uuid
from typing import List, Dict, Tuple, Optional

import chromadb
from chromadb.config import Settings
from pypdf import PdfReader


# ---------------------------
# Paths / Collections
# ---------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
CHROMA_PATH = os.path.join(PROJECT_ROOT, "chroma_db")

PARENTS_COLLECTION = "rt_parents"
CHILDREN_COLLECTION = "rt_children"


# ---------------------------
# Chunking configuration
# ---------------------------
PARENT_CHARS = 2200          # size of each parent chunk (characters)
PARENT_OVERLAP = 200         # overlap between parent chunks
CHILD_CHARS = 600            # size of each child chunk
CHILD_OVERLAP = 120          # overlap between child chunks


# ---------------------------
# Helpers
# ---------------------------
def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse very long whitespace runs
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_md_or_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return _clean_text(f.read())


def _read_pdf(path: str) -> str:
    reader = PdfReader(path)
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return _clean_text("\n".join(pages))


def _is_supported_file(filename: str) -> bool:
    fn = filename.lower()
    return fn.endswith(".md") or fn.endswith(".txt") or fn.endswith(".pdf")


def _iter_files(root: str) -> List[str]:
    out = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if _is_supported_file(fn):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def _rel_path(abs_path: str, root: str) -> str:
    return os.path.relpath(abs_path, root).replace("\\", "/")


def _department_from_rel(rel: str) -> str:
    """
    Your structure:
      data/aws/...                  -> aws
      data/hipaa/...                -> hipaa
      data/internal/hr/...          -> hr
      data/internal/engineering/... -> engineering

    We normalize:
      internal/<dept>/... -> <dept>
      otherwise           -> first folder name
    """
    parts = rel.split("/")
    if not parts:
        return "unknown"

    if parts[0] == "internal" and len(parts) > 1:
        return parts[1]  # hr, engineering, legal_internal, ...
    return parts[0]      # aws, hipaa, ...


def _split_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    Simple character splitter with overlap.
    Keeps chunks non-empty and trimmed.
    """
    text = _clean_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap)

    return chunks


def _make_parent_id() -> str:
    return str(uuid.uuid4())


# ---------------------------
# Core ingestion
# ---------------------------
def ingest(clear_existing: bool = True) -> int:
    """
    Returns total number of child chunks inserted.
    """

    if not os.path.isdir(DATA_ROOT):
        raise FileNotFoundError(f"data folder not found at: {DATA_ROOT}")

    client = chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )

    parents_col = client.get_or_create_collection(PARENTS_COLLECTION)
    children_col = client.get_or_create_collection(CHILDREN_COLLECTION)

    if clear_existing:
        # Clear old data (simple + deterministic for demo)
        try:
            parents_col.delete(where={})
        except Exception:
            pass
        try:
            children_col.delete(where={})
        except Exception:
            pass

    files = _iter_files(DATA_ROOT)
    total_children = 0

    for abs_path in files:
        rel = _rel_path(abs_path, DATA_ROOT)
        dept = _department_from_rel(rel)

        # Load text
        if abs_path.lower().endswith(".pdf"):
            text = _read_pdf(abs_path)
        else:
            text = _read_md_or_txt(abs_path)

        if not text:
            continue

        # Parent chunks
        parent_chunks = _split_text(text, PARENT_CHARS, PARENT_OVERLAP)

        # Prepare parent inserts
        parent_ids: List[str] = []
        parent_docs: List[str] = []
        parent_metas: List[Dict] = []

        # Prepare child inserts
        child_ids: List[str] = []
        child_docs: List[str] = []
        child_metas: List[Dict] = []

        for parent_index, parent_text in enumerate(parent_chunks):
            parent_id = _make_parent_id()

            parent_ids.append(parent_id)
            parent_docs.append(parent_text)
            parent_metas.append({
                "department": dept,
                "source": rel.replace("internal/", ""),  # prettier source display
                "rel_path": rel,
                "parent_index": parent_index,
            })

            # Child chunks from this parent
            child_chunks = _split_text(parent_text, CHILD_CHARS, CHILD_OVERLAP)
            for child_index, child_text in enumerate(child_chunks):
                cid = f"{parent_id}:{child_index}"
                child_ids.append(cid)
                child_docs.append(child_text)
                child_metas.append({
                    "department": dept,
                    "source": rel.replace("internal/", ""),
                    "rel_path": rel,
                    "parent_id": parent_id,
                    "parent_index": parent_index,
                    "child_index": child_index,
                })

        # Insert into Chroma
        if parent_ids:
            parents_col.add(ids=parent_ids, documents=parent_docs, metadatas=parent_metas)

        if child_ids:
            children_col.add(ids=child_ids, documents=child_docs, metadatas=child_metas)
            total_children += len(child_ids)

        print(f"[ingest] {rel} -> {len(child_ids)} child chunks (dept={dept})")

    print(f"[ingest] done. total_child_chunks={total_children}")
    return total_children


# ---------------------------
# Entry points
# ---------------------------
def run_ingestion() -> int:
    """
    Callable from Streamlit UI.
    """
    return ingest(clear_existing=True)


def main():
    run_ingestion()


if __name__ == "__main__":
    main()
