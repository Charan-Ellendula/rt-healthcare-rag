# ingestion/ingest.py
import os
import glob
import uuid
from typing import List, Dict, Any, Tuple

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from pypdf import PdfReader

SUPPORTED_EXTS = {".txt", ".md", ".pdf"}

def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def read_pdf(path: str) -> str:
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    return "\n".join(parts)

def load_document(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return read_pdf(path)
    return read_text_file(path)

def normalize_ws(text: str) -> str:
    return " ".join(text.split())

def parent_split(text: str, max_chars: int = 2200) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return []
    parents, cur = [], ""
    for p in paras:
        candidate = (cur + "\n\n" + p).strip() if cur else p
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            if cur:
                parents.append(cur)
            if len(p) > max_chars:
                start = 0
                while start < len(p):
                    end = min(start + max_chars, len(p))
                    parents.append(p[start:end])
                    if end == len(p):
                        break
                    start = max(0, end - 200)
                cur = ""
            else:
                cur = p
    if cur:
        parents.append(cur)
    return parents

def child_split(parent_text: str, chunk_size: int = 600, overlap: int = 120) -> List[str]:
    t = normalize_ws(parent_text)
    if not t:
        return []
    chunks, start = [], 0
    while start < len(t):
        end = min(start + chunk_size, len(t))
        chunks.append(t[start:end])
        if end == len(t):
            break
        start = max(0, end - overlap)
    return chunks

def infer_department(data_root: str, file_path: str) -> str:
    rel = os.path.relpath(file_path, data_root)
    parts = rel.split(os.sep)
    return parts[0] if parts else "unknown"

def collect_files(data_root: str) -> List[str]:
    all_files = []
    for ext in SUPPORTED_EXTS:
        all_files.extend(glob.glob(os.path.join(data_root, "**", f"*{ext}"), recursive=True))
    return sorted(set(all_files))

def build_parent_and_children(data_root: str, file_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw = load_document(file_path)
    department = infer_department(data_root, file_path)
    rel_path = os.path.relpath(file_path, data_root)

    parents = parent_split(raw, max_chars=2200)
    parent_rows = []
    child_rows = []

    for parent_index, ptxt in enumerate(parents):
        parent_id = str(uuid.uuid4())
        parent_rows.append({
            "id": parent_id,
            "text": ptxt,
            "metadata": {
                "source": rel_path,
                "department": department,
                "parent_index": parent_index,
            }
        })

        for child_index, ctxt in enumerate(child_split(ptxt, 600, 120)):
            child_rows.append({
                "id": str(uuid.uuid4()),
                "text": ctxt,
                "metadata": {
                    "source": rel_path,
                    "department": department,
                    "parent_id": parent_id,
                    "parent_index": parent_index,
                    "child_index": child_index,
                }
            })

    return parent_rows, child_rows

def clear_collection(col) -> None:
    try:
        existing = col.get(include=["ids"])
        ids = existing.get("ids") or []
        if ids:
            col.delete(ids=ids)
    except Exception:
        pass

def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    data_root = os.path.join(project_root, "data")
    db_dir = os.path.join(project_root, "chroma_db")

    if not os.path.isdir(data_root):
        raise SystemExit(f"Missing data folder at: {data_root}")

    print(f"[ingest] data_root = {data_root}")
    print(f"[ingest] chroma_db = {db_dir}")

    client = chromadb.PersistentClient(path=db_dir, settings=Settings(anonymized_telemetry=False))
    parents_col = client.get_or_create_collection(name="rt_parents")
    children_col = client.get_or_create_collection(name="rt_children")

    embedder = SentenceTransformer("all-MiniLM-L6-v2")

    files = collect_files(data_root)
    if not files:
        raise SystemExit("No supported files found under data/ (txt/md/pdf).")

    print("[ingest] clearing collections...")
    clear_collection(parents_col)
    clear_collection(children_col)

    total_parents = 0
    total_children = 0

    for fp in files:
        parent_rows, child_rows = build_parent_and_children(data_root, fp)
        if not child_rows:
            continue

        # store parents (no embeddings needed)
        parents_col.add(
            ids=[p["id"] for p in parent_rows],
            documents=[p["text"] for p in parent_rows],
            metadatas=[p["metadata"] for p in parent_rows],
        )

        # store children with embeddings
        texts = [c["text"] for c in child_rows]
        embeddings = embedder.encode(texts, normalize_embeddings=True).tolist()

        children_col.add(
            ids=[c["id"] for c in child_rows],
            documents=texts,
            metadatas=[c["metadata"] for c in child_rows],
            embeddings=embeddings,
        )

        total_parents += len(parent_rows)
        total_children += len(child_rows)
        print(f"[ingest] {os.path.relpath(fp, data_root)} -> parents={len(parent_rows)} children={len(child_rows)}")

    print(f"[ingest] done. total_parents={total_parents} total_children={total_children}")

if __name__ == "__main__":
    main()
