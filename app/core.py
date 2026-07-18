# app/core.py
# ============================================================
# Shared RAG + RBAC logic used by the FastAPI backend (server.py).
# Extracted from the old Streamlit app so it has a single home.
# ============================================================

import os
from typing import Any, Dict, List, Tuple

import chromadb
import google.generativeai as genai
import yaml
from chromadb.config import Settings
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from ingestion.ingest import run_ingestion

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

TOP_K_CHILD = 8
MAX_PARENTS_IN_CONTEXT = 3
MAX_HISTORY_TURNS = 4


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def allowed_departments_for_role(rules: Dict[str, Any], role: str) -> List[str]:
    roles = rules.get("roles", {})
    return (roles.get(role, {}) or {}).get("allow_departments", []) or []


def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    max_items = MAX_HISTORY_TURNS * 2
    return history[-max_items:] if len(history) > max_items else history


def format_history(history: List[Dict[str, str]]) -> str:
    lines = []
    for h in history:
        prefix = "User: " if h["role"] == "user" else "Assistant: "
        lines.append(prefix + h["text"])
    return "\n".join(lines).strip()


def retrieve_children(children_col, embedder, question: str, allowed_depts: List[str], k: int = TOP_K_CHILD) -> List[Dict[str, Any]]:
    q_emb = embedder.encode([question], normalize_embeddings=True).tolist()[0]
    where_filter = {"department": {"$in": allowed_depts}} if allowed_depts else {"department": "__none__"}

    res = children_col.query(
        query_embeddings=[q_emb],
        n_results=k,
        where=where_filter,
        include=["documents", "metadatas", "distances"],
    )

    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]

    out: List[Dict[str, Any]] = []
    seen = set()
    for doc, meta, dist in zip(docs, metas, dists):
        if not meta:
            continue
        key = (meta.get("source"), meta.get("parent_id"), meta.get("child_index"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": doc, "metadata": meta, "distance": float(dist)})

    return out


def build_parent_context(parents_col, retrieved_children: List[Dict[str, Any]], max_parents: int = MAX_PARENTS_IN_CONTEXT) -> Tuple[List[str], List[Dict[str, Any]]]:
    parent_ids: List[str] = []
    seen = set()

    for r in retrieved_children:
        pid = r["metadata"].get("parent_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        parent_ids.append(pid)
        if len(parent_ids) >= max_parents:
            break

    if not parent_ids:
        return [], []

    got = parents_col.get(ids=parent_ids, include=["documents", "metadatas"])
    parent_docs = got.get("documents") or []
    parent_metas = got.get("metadatas") or []

    context_blocks: List[str] = []
    citations: List[Dict[str, Any]] = []

    for i, (ptxt, pmeta) in enumerate(zip(parent_docs, parent_metas), start=1):
        context_blocks.append(f"[{i}] {ptxt}")
        citations.append(
            {
                "n": i,
                "source": (pmeta or {}).get("source"),
                "department": (pmeta or {}).get("department"),
                "parent_index": (pmeta or {}).get("parent_index"),
            }
        )

    return context_blocks, citations


def build_prompt(question: str, allowed_depts: List[str], history: List[Dict[str, str]], context_blocks: List[str]) -> str:
    history_text = format_history(history)
    return f"""
You are a helpful, professional enterprise assistant.

RULES:
- Answer naturally in a clear paragraph (unless the user asks for bullets).
- Do NOT mention file names, paths, chunks, embeddings, vector DB, or internal system behavior.
- ONLY use information from the context blocks.
- If the context is insufficient, say exactly:
  "I don't have enough information in the allowed documents."
- Add citations ONLY at the end like: Sources: [1], [2]

RBAC allowed departments:
{allowed_depts}

Conversation so far (continuity only; do not invent facts):
{history_text if history_text else "(none)"}

User question:
{question}

Context blocks:
{chr(10).join(context_blocks)}
""".strip()


def is_streamlit_cloud() -> bool:
    if os.path.exists("/mount/src"):
        return True
    if os.environ.get("STREAMLIT_SERVER_RUNNING") == "1":
        return True
    return False


class RagRuntime:
    """Holds the heavy, shared singletons: chroma client, collections, embedder, gemini model."""

    def __init__(self) -> None:
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY. Set it in .env or the environment.")

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)

        if is_streamlit_cloud():
            self.client = chromadb.Client(Settings(anonymized_telemetry=False))
        else:
            self.client = chromadb.PersistentClient(
                path=os.path.join(PROJECT_ROOT, "chroma_db"),
                settings=Settings(anonymized_telemetry=False),
            )

        self.parents_col = self.client.get_or_create_collection("rt_parents")
        self.children_col = self.client.get_or_create_collection("rt_children")

        if self.children_col.count() == 0:
            run_ingestion(clear_existing=True, client=self.client)

        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")

        self.rules = load_yaml(os.path.join(PROJECT_ROOT, "rbac_rules.yaml"))
        self.users = load_yaml(os.path.join(PROJECT_ROOT, "users.yaml")).get("users", {})

    def answer(self, question: str, allowed_depts: List[str], history: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, Any]]]:
        retrieved_children = retrieve_children(self.children_col, self.embedder, question, allowed_depts)
        context_blocks, citations = build_parent_context(self.parents_col, retrieved_children)

        if not context_blocks:
            return "I don't have enough information in the allowed documents.", []

        prompt = build_prompt(question, allowed_depts, history, context_blocks)
        resp = self.model.generate_content(prompt)
        answer = (getattr(resp, "text", "") or "").strip()
        return answer, citations
