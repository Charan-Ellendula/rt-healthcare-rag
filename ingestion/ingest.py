# ingestion/ingest.py
# ============================================================
# Ingestion pipeline:
# - Reads files from /data (md/txt/pdf)
# - Parent/Child chunking
# - Stores:
#     rt_parents: larger context chunks (parents)
#     rt_children: smaller semantic chunks (children)
#
# IMPORTANT DESIGN:
# - run_ingestion() accepts an optional `client`.
#   * Local CLI run: client=None -> PersistentClient(chroma_db/)
#   * Streamlit Cloud: UI passes in-memory client -> index builds in same memory DB
# ============================================================

import os
import re
import uuid
from typing import List, Optional

import chromadb
from chromadb.config import Settings

# PDF reader
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None  # handled gracefully


# ---------------------------
# Paths & Collections
# ---------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_ROOT = os.path.join(PROJECT_ROOT, "data")
CHROMA_PATH = os.path.join(PROJECT_ROOT, "chroma_db")

PARENTS_COLLECTION = "rt_parents"
CHILDREN_COLLECTION = "rt_children"


# ---------------------------
# Chunking params
# ---------------------------
PARENT_CHARS = 2200
PARENT_OVERLAP = 200
CHILD_CHARS = 600
CHILD_OVERLAP = 120


# ---------------------------
# Text utilities
# ---------------------------
def _clean_text(t: str) -> str:
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _split_text(text: str, size: int, overlap: int) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []

    chunks = []
    i = 0
    n = len(text)

    while i < n:
        j = min(i + size, n)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j >= n:
            break
        i = max(0, j - overlap)

    return chunks


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return _clean_text(f.read())


def _read_pdf(path: str) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed but a PDF was found. Add `pypdf` to requirements.txt")

    r = PdfReader(path)
    out = []
    for p in r.pages:
        out.append(p.extract_text() or "")
    return _clean_text("\n".join(out))


def _dept_from_rel(rel: str) -> str:
    # Example data layout:
    # data/internal/hr/xxx.md -> dept = hr
    # data/hr/xxx.md -> dept = hr
    parts = rel.split("/")
    if parts and parts[0] == "internal" and len(parts) > 1:
        return parts[1]
    return parts[0] if parts else "unknown"


def _source_display(rel: str) -> str:
    # nicer source string for metadata
    return rel.replace("internal/", "")


# ---------------------------
# Client creation (local default)
# ---------------------------
def _persistent_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(
        path=CHROMA_PATH,
        settings=Settings(anonymized_telemetry=False),
    )


# ---------------------------
# Main ingestion
# ---------------------------
def run_ingestion(clear_existing: bool = True, client: Optional[chromadb.Client] = None) -> int:
    """
    Build the index into Chroma.

    Args:
      clear_existing: wipe collections before adding
      client: if provided, ingestion writes into this client (used by Streamlit Cloud in-memory)

    Returns:
      total number of child chunks added
    """
    if not os.path.isdir(DATA_ROOT):
        raise FileNotFoundError(f"Missing data folder: {DATA_ROOT}")

    if client is None:
        client = _persistent_client()

    parents_col = client.get_or_create_collection(PARENTS_COLLECTION)
    children_col = client.get_or_create_collection(CHILDREN_COLLECTION)

    if clear_existing:
        try:
            parents_col.delete(where={})
        except Exception:
            pass
        try:
            children_col.delete(where={})
        except Exception:
            pass

    total_children = 0

    for dirpath, _, filenames in os.walk(DATA_ROOT):
        for fn in filenames:
            low = fn.lower()
            if not (low.endswith(".md") or low.endswith(".txt") or low.endswith(".pdf")):
                continue

            abs_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_path, DATA_ROOT).replace("\\", "/")
            dept = _dept_from_rel(rel)

            # Read file
            if low.endswith(".pdf"):
                text = _read_pdf(abs_path)
            else:
                text = _read_text_file(abs_path)

            if not text:
                continue

            # Parent chunks
            parents = _split_text(text, PARENT_CHARS, PARENT_OVERLAP)

            parent_ids, parent_docs, parent_metas = [], [], []
            child_ids, child_docs, child_metas = [], [], []

            for p_idx, ptxt in enumerate(parents):
                pid = str(uuid.uuid4())
                parent_ids.append(pid)
                parent_docs.append(ptxt)
                parent_metas.append(
                    {
                        "department": dept,
                        "source": _source_display(rel),
                        "rel_path": rel,
                        "parent_index": p_idx,
                    }
                )

                # Child chunks from each parent
                children = _split_text(ptxt, CHILD_CHARS, CHILD_OVERLAP)
                for c_idx, ctxt in enumerate(children):
                    cid = f"{pid}:{c_idx}"
                    child_ids.append(cid)
                    child_docs.append(ctxt)
                    child_metas.append(
                        {
                            "department": dept,
                            "source": _source_display(rel),
                            "rel_path": rel,
                            "parent_id": pid,
                            "parent_index": p_idx,
                            "child_index": c_idx,
                        }
                    )

            if parent_ids:
                parents_col.add(ids=parent_ids, documents=parent_docs, metadatas=parent_metas)

            if child_ids:
                children_col.add(ids=child_ids, documents=child_docs, metadatas=child_metas)
                total_children += len(child_ids)

            print(f"[ingest] {rel} -> {len(child_ids)} child chunks (dept={dept})")

    print(f"[ingest] done total_child_chunks={total_children}")
    return total_children


def main():
    run_ingestion(clear_existing=True, client=None)


if __name__ == "__main__":
    main()
