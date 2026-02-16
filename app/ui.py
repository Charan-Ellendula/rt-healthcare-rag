import time
# app/ui.py
# ============================================================
# Streamlit UI for RBAC RAG (Chroma + Gemini)
# - Login with users.yaml (demo users)
# - Role auto-selected from user profile
# - RBAC enforced at retrieval time (Chroma metadata filter)
# - Parent/Child RAG: retrieve children, fetch parents for coherence
# - Conversational memory (Streamlit session_state)
# - Audit logging (JSONL)
# - Cloud fix: auto-build Chroma index on first run (empty DB)
# ============================================================

import os
import sys
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, List

import yaml
import streamlit as st
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import google.generativeai as genai


# ---------------------------
# Config
# ---------------------------
AUDIT_LOG_PATH = "audit_log.jsonl"
TOP_K_CHILD = 12
MAX_PARENTS_IN_CONTEXT = 4
MAX_HISTORY_TURNS = 6


# ---------------------------
# Utilities
# ---------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(record: Dict[str, Any], project_root: str) -> None:
    path = os.path.join(project_root, AUDIT_LOG_PATH)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def allowed_departments_for_role(rules: Dict[str, Any], role: str) -> List[str]:
    roles = rules.get("roles", {})
    if role not in roles:
        return []
    return roles[role].get("allow_departments", [])


def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    max_items = MAX_HISTORY_TURNS * 2
    return history[-max_items:] if len(history) > max_items else history


def format_history(history: List[Dict[str, str]]) -> str:
    lines = []
    for h in history:
        lines.append(("User: " if h["role"] == "user" else "Assistant: ") + h["text"])
    return "\n".join(lines).strip()


# ---------------------------
# Retrieval (children) + parent fetch
# ---------------------------
def retrieve_children(children_col, embedder, question: str, allowed_depts: List[str], k: int = TOP_K_CHILD):
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

    seen = set()
    out = []
    for doc, meta, dist in zip(docs, metas, dists):
        if not meta:
            continue
        key = (meta.get("source"), meta.get("parent_id"), meta.get("child_index"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": doc, "metadata": meta, "distance": float(dist)})

    return out


def build_parent_context_from_ids(parents_col, retrieved_children: List[Dict[str, Any]], max_parents: int = MAX_PARENTS_IN_CONTEXT):
    parent_ids = []
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

    context_blocks = []
    citations = []
    for i, (ptxt, pmeta) in enumerate(zip(parent_docs, parent_metas), start=1):
        context_blocks.append(f"[{i}] {ptxt}")
        citations.append({
            "n": i,
            "source": (pmeta or {}).get("source"),
            "department": (pmeta or {}).get("department"),
            "parent_index": (pmeta or {}).get("parent_index"),
        })

    return context_blocks, citations


def build_prompt(question: str, allowed_depts: List[str], history: List[Dict[str, str]], context_blocks: List[str]) -> str:
    history_text = format_history(history)
    return f"""
You are a professional enterprise AI assistant.

INSTRUCTIONS:
- Answer naturally and clearly, as if speaking to an employee.
- Do NOT mention file paths, chunking, metadata, or internal system behavior.
- Do NOT say "based on the document" or "the context says".
- Prefer a single clean paragraph unless the user asks for a list.
- Only use information from the provided context blocks.
- If the context is insufficient, say: "I don't have enough information in the allowed documents."
- Add citations ONLY at the very end like: Sources: [1], [2]

RBAC allowed departments:
{allowed_depts}

Conversation so far (for continuity, not for new facts):
{history_text if history_text else "(none)"}

User question:
{question}

Context blocks:
{chr(10).join(context_blocks)}
""".strip()


# ---------------------------
# Runtime initialization
# ---------------------------
def init_runtime():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Load local .env if present (local dev); Streamlit Cloud uses Secrets->env vars
    load_dotenv(os.path.join(project_root, ".env"))

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()

    if not api_key:
        st.error("Missing GEMINI_API_KEY (set it in Streamlit Secrets or local .env)")
        st.stop()

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    # Chroma (2 collections)
    client = chromadb.PersistentClient(
        path=os.path.join(project_root, "chroma_db"),
        settings=Settings(anonymized_telemetry=False),
    )
    parents_col = client.get_or_create_collection("rt_parents")
    children_col = client.get_or_create_collection("rt_children")

# ✅ Cloud fix: build index once if empty (fresh Streamlit Cloud container)
try:
    if children_col.count() == 0:
        st.warning("First-time setup: building the vector index. Please wait (1–3 minutes)...")

        # Ensure project root is on the Python path so "ingestion" can be imported on Streamlit Cloud
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from ingestion.ingest import run_ingestion
        run_ingestion()

        st.success("Vector index built successfully. Reloading...")
        st.rerun()

except Exception as e:
    st.error(f"Index build failed: {e}")
    st.stop()

# ---------------------------
# Login UI
# ---------------------------
def login_ui(users: Dict[str, Any]):
    st.title("RT Healthcare RAG (RBAC Demo)")
    st.subheader("Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    login = st.button("Login")

    if login:
        u = users.get(username)
        if not u or u.get("password") != password:
            st.error("Invalid username or password")
            return

        st.session_state["authed"] = True
        st.session_state["username"] = username
        st.session_state["role"] = u.get("role")
        st.session_state["session_id"] = str(uuid.uuid4())
        st.session_state["history"] = []
        st.success(f"Logged in as {username} (role={st.session_state['role']})")
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

    # Render previous messages
    for h in st.session_state["history"]:
        with st.chat_message(h["role"]):
            st.write(h["text"])

    question = st.chat_input("Ask a question…")
    if not question:
        return

    # User turn
    st.session_state["history"].append({"role": "user", "text": question})
    st.session_state["history"] = trim_history(st.session_state["history"])
    with st.chat_message("user"):
        st.write(question)

    # Retrieve
    retrieved_children = retrieve_children(children_col, embedder, question, allowed_depts)
    context_blocks, citations = build_parent_context_from_ids(parents_col, retrieved_children)

    if not context_blocks:
        answer = "I don't have enough information in the allowed documents."
    else:
        prompt = build_prompt(question, allowed_depts, st.session_state["history"][:-1], context_blocks)
        resp = model.generate_content(prompt)
        answer = (resp.text or "").strip()

    # Assistant turn
    st.session_state["history"].append({"role": "assistant", "text": answer})
    st.session_state["history"] = trim_history(st.session_state["history"])

    with st.chat_message("assistant"):
        st.write(answer)

    # Audit log
    append_audit({
        "ts": utc_now_iso(),
        "session_id": session_id,
        "username": username,
        "role": role,
        "allowed_departments": allowed_depts,
        "question": question,
        "mode": "rag",
        "retrieved": citations,
        "answer": answer,
    }, project_root)


# ---------------------------
# Streamlit main
# ---------------------------
def main():
    project_root, model, parents_col, children_col, embedder, rules, users = init_runtime()

    if "authed" not in st.session_state:
        st.session_state["authed"] = False

    # Sidebar actions
    with st.sidebar:
        st.header("Controls")
        if st.session_state.get("authed"):
            if st.button("Logout"):
                for k in ["authed", "username", "role", "session_id", "history"]:
                    if k in st.session_state:
                        del st.session_state[k]
                st.rerun()
        st.caption("Tip: On first cloud run, the app auto-builds the vector index.")

    if not st.session_state["authed"]:
        login_ui(users)
        return

    chat_ui(project_root, model, parents_col, children_col, embedder, rules)


if __name__ == "__main__":
    main()
