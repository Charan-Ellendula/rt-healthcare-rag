# app/ui.py
# ============================================================
# Streamlit UI:
# - Login (users.yaml) -> role auto-assigned
# - RBAC enforced at retrieval time (Chroma where filter)
# - Parent/Child RAG:
#     * semantic search on rt_children
#     * expand to rt_parents for coherent context
# - Conversational memory
# - Audit logging JSONL
#
# Cloud stability:
# - Streamlit Cloud uses in-memory Chroma (avoids HNSW disk errors)
# - Local uses persistent Chroma at chroma_db/
# - On first run (empty rt_children), build index by calling run_ingestion(client=client)
#   so UI + ingestion share the same in-memory client on Cloud.
# ============================================================

import os
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

import yaml
import streamlit as st
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import google.generativeai as genai

import os, sys
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ingestion.ingest import run_ingestion


# ---------------------------
# Config knobs
# ---------------------------
AUDIT_LOG_PATH = "audit_log.jsonl"
TOP_K_CHILD = 8
MAX_PARENTS_IN_CONTEXT = 3
MAX_HISTORY_TURNS = 4


# ---------------------------
# Utility helpers
# ---------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def append_audit(record: Dict[str, Any], project_root: str) -> None:
    path = os.path.join(project_root, AUDIT_LOG_PATH)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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


# ---------------------------
# Cloud detection (simple + robust)
# ---------------------------
def is_streamlit_cloud() -> bool:
    # Streamlit Cloud often runs from /mount/src/<repo>
    if os.path.exists("/mount/src"):
        return True
    # fallback env hints (may or may not exist)
    if os.environ.get("STREAMLIT_SERVER_RUNNING") == "1":
        return True
    return False


# ---------------------------
# Cached heavy resources (speed)
# ---------------------------
@st.cache_resource
def get_embedder():
    return SentenceTransformer("all-MiniLM-L6-v2")


@st.cache_resource
def get_gemini_model(model_name: str, api_key: str):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


@st.cache_resource
def get_chroma_client(project_root: str):
    """
    Cloud: in-memory client
    Local: persistent client at chroma_db/
    """
    if is_streamlit_cloud():
        return chromadb.Client(Settings(anonymized_telemetry=False))
    return chromadb.PersistentClient(
        path=os.path.join(project_root, "chroma_db"),
        settings=Settings(anonymized_telemetry=False),
    )


def get_collections(client):
    parents_col = client.get_or_create_collection("rt_parents")
    children_col = client.get_or_create_collection("rt_children")
    return parents_col, children_col


# ---------------------------
# Retrieval + Parent expansion
# ---------------------------
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


# ---------------------------
# Runtime init
# ---------------------------
def init_runtime():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # local: load .env; cloud: secrets become env vars
    load_dotenv(os.path.join(project_root, ".env"))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

    if not api_key:
        st.error("Missing GEMINI_API_KEY. Set it in Streamlit Secrets (or local .env).")
        st.stop()

    model = get_gemini_model(model_name, api_key)

    client = get_chroma_client(project_root)
    parents_col, children_col = get_collections(client)

    # Build index if empty
    if children_col.count() == 0:
        st.warning("First-time setup: building the vector index. Please wait (1–3 minutes)...")
        # Critical: pass the SAME client so cloud builds in the same in-memory DB
        run_ingestion(clear_existing=True, client=client)
        st.success("Index built. Reloading...")
        st.rerun()

    embedder = get_embedder()

    rules = load_yaml(os.path.join(project_root, "rbac_rules.yaml"))
    users = load_yaml(os.path.join(project_root, "users.yaml")).get("users", {})

    return project_root, model, parents_col, children_col, embedder, rules, users


# ---------------------------
# Login UI
# ---------------------------
def login_ui(users: Dict[str, Any]):
    st.title("RT Healthcare RAG (RBAC Demo)")
    st.subheader("Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        u = users.get(username)
        if not u or u.get("password") != password:
            st.error("Invalid username or password.")
            return

        st.session_state["authed"] = True
        st.session_state["username"] = username
        st.session_state["role"] = u.get("role")
        st.session_state["session_id"] = str(uuid.uuid4())
        st.session_state["history"] = []
        st.rerun()


# ---------------------------
# Chat UI
# ---------------------------
def chat_ui(project_root, model, parents_col, children_col, embedder, rules):
    st.title("RT Healthcare RAG (RBAC Demo)")

    username = st.session_state["username"]
    role = st.session_state["role"]
    session_id = st.session_state["session_id"]
    allowed_depts = allowed_departments_for_role(rules, role)

    st.caption(f"User: {username} | Role: {role} | Allowed: {', '.join(allowed_depts)} | Session: {session_id}")

    # Render history
    for h in st.session_state["history"]:
        with st.chat_message(h["role"]):
            st.write(h["text"])

    question = st.chat_input("Ask a question…")
    if not question:
        return

    # Save user message
    st.session_state["history"].append({"role": "user", "text": question})
    st.session_state["history"] = trim_history(st.session_state["history"])
    with st.chat_message("user"):
        st.write(question)

    # Retrieve + build context
    retrieved_children = retrieve_children(children_col, embedder, question, allowed_depts)
    context_blocks, citations = build_parent_context(parents_col, retrieved_children)

    # Answer
    if not context_blocks:
        answer = "I don't have enough information in the allowed documents."
    else:
        prompt = build_prompt(question, allowed_depts, st.session_state["history"][:-1], context_blocks)
        resp = model.generate_content(prompt)
        answer = (getattr(resp, "text", "") or "").strip()

    # Save assistant message
    st.session_state["history"].append({"role": "assistant", "text": answer})
    st.session_state["history"] = trim_history(st.session_state["history"])

    with st.chat_message("assistant"):
        st.write(answer)

    # Audit log
    append_audit(
        {
            "ts": utc_now_iso(),
            "session_id": session_id,
            "username": username,
            "role": role,
            "allowed_departments": allowed_depts,
            "question": question,
            "retrieved": citations,
            "answer": answer,
        },
        project_root,
    )


# ---------------------------
# Entry point
# ---------------------------
def main():
    project_root, model, parents_col, children_col, embedder, rules, users = init_runtime()

    if "authed" not in st.session_state:
        st.session_state["authed"] = False

    with st.sidebar:
        st.header("Controls")
        if st.session_state.get("authed"):
            if st.button("Logout"):
                for k in ["authed", "username", "role", "session_id", "history"]:
                    if k in st.session_state:
                        del st.session_state[k]
                st.rerun()

    if not st.session_state["authed"]:
        login_ui(users)
        return

    chat_ui(project_root, model, parents_col, children_col, embedder, rules)


if __name__ == "__main__":
    main()
