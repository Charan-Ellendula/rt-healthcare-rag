# app/main.py
# ============================================================
# Clean RAG + RBAC (Chroma) + Gemini
# + Audit logging (JSONL)
# + Persistent sessions (JSON)
# + Conversational history (bounded)
# + Parent/Child retrieval (2 collections)
#
# Collections:
#   - rt_children: embedded child chunks (retrieval)
#   - rt_parents : parent chunks (coherent generation context)
# ============================================================

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List

import yaml
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from dotenv import load_dotenv
import google.generativeai as genai


# ============================================================
# CONFIG
# ============================================================

AUDIT_LOG_PATH = "audit_log.jsonl"
SESSIONS_DIR = "sessions"

MAX_HISTORY_TURNS = 6          # keep last N user/assistant pairs in memory
TOP_K_CHILD = 12               # retrieve this many child chunks
MAX_PARENTS_IN_CONTEXT = 4     # include top unique parents for coherence


# ============================================================
# TIME + FILE UTILITIES
# ============================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs(project_root: str) -> None:
    os.makedirs(os.path.join(project_root, SESSIONS_DIR), exist_ok=True)


# ============================================================
# AUDIT LOGGING (JSONL)
# Each question results in one appended JSON object line.
# ============================================================

def append_audit(record: Dict[str, Any], project_root: str) -> None:
    path = os.path.join(project_root, AUDIT_LOG_PATH)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ============================================================
# SESSION PERSISTENCE (JSON)
# Saves/loads conversation history + role context.
# ============================================================

def save_session(project_root: str, session: Dict[str, Any]) -> str:
    ensure_dirs(project_root)
    sid = session["session_id"]
    path = os.path.join(project_root, SESSIONS_DIR, f"session_{sid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return path


def load_session(project_root: str, session_id: str) -> Dict[str, Any]:
    path = os.path.join(project_root, SESSIONS_DIR, f"session_{session_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# RBAC (YAML RULES)
# ============================================================

def load_rbac_rules(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def allowed_departments_for_role(rules: Dict[str, Any], role: str) -> List[str]:
    roles = rules.get("roles", {})
    if role not in roles:
        return []
    return roles[role].get("allow_departments", [])


# ============================================================
# INTENT ROUTER FOR "ACCESS" QUESTIONS
# These should be answered from RBAC rules, not RAG docs.
# ============================================================

def is_access_question(q: str) -> bool:
    q = q.lower().strip()
    triggers = [
        "what do i have access",
        "what all i have access",
        "what can i access",
        "my access",
        "my permissions",
        "what am i allowed",
        "what am i authorized",
        "which documents can i see",
        "which departments can i access",
        "access rights",
        "rbac",
        "permissions",
        "authorized",
    ]
    return any(t in q for t in triggers)


def answer_access_from_rbac(role: str, allowed_depts: List[str]) -> str:
    if not allowed_depts:
        return (
            f"As {role}, you don’t currently have any configured access. "
            "Ask an admin to add allowed departments for your role."
        )
    dept_list = ", ".join(allowed_depts)
    return (
        f"As {role}, you can access documents in these areas: {dept_list}. "
        "If you need access to additional areas, request an update to your role permissions."
    )


# ============================================================
# RETRIEVAL (CHILD COLLECTION)
# - Embed question
# - Chroma query with RBAC metadata filter
# - Deduplicate by (source, parent_id, child_index)
# ============================================================

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


# ============================================================
# CONTEXT BUILDING (PARENT COLLECTION)
# - Collect top unique parent_ids (rank order)
# - Fetch parent texts from rt_parents by parent_id
# - Build coherent context blocks for the LLM
# ============================================================

def build_parent_context_from_ids(
    parents_col,
    retrieved_children: List[Dict[str, Any]],
    max_parents: int = MAX_PARENTS_IN_CONTEXT,
):
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


# ============================================================
# CONVERSATION HISTORY
# - Bounded memory for continuity
# ============================================================

def trim_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    max_items = MAX_HISTORY_TURNS * 2
    return history[-max_items:] if len(history) > max_items else history


def format_history(history: List[Dict[str, str]]) -> str:
    lines = []
    for h in history:
        if h["role"] == "user":
            lines.append(f"User: {h['text']}")
        else:
            lines.append(f"Assistant: {h['text']}")
    return "\n".join(lines).strip()


# ============================================================
# PROMPTING (NATURAL ASSISTANT STYLE)
# - No metadata/chunk talk
# - No "based on the doc"
# - Clean paragraph
# - Citations only at the end
# ============================================================

def build_prompt(question: str, allowed_depts: List[str], history: List[Dict[str, str]], context_blocks: List[str]) -> str:
    history_text = format_history(history)

    return f"""
You are a professional enterprise AI assistant.

INSTRUCTIONS:
- Answer naturally and clearly, as if speaking to an employee.
- Do NOT mention file paths, chunking, metadata, or internal system behavior.
- Do NOT say "based on the document" or "the context says".
- Prefer a single clean paragraph unless the user explicitly asks for a list.
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


# ============================================================
# CLI COMMANDS
# ============================================================

def print_commands():
    print("\nCommands:")
    print("  exit            - quit (autosaves session)")
    print("  reset           - clear conversation history for current session")
    print("  save            - save session to disk")
    print("  load <id>       - load a session by id")
    print("  newsession      - start a new session id (keeps same role)")
    print("  help            - show commands\n")


# ============================================================
# MAIN APP LOOP
# ============================================================

def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    rules_path = os.path.join(project_root, "rbac_rules.yaml")
    db_dir = os.path.join(project_root, "chroma_db")

    ensure_dirs(project_root)

    # ---- Load Gemini config ----
    load_dotenv(os.path.join(project_root, ".env"))
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    if not api_key:
        raise SystemExit("Missing GEMINI_API_KEY in .env")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    # ---- Load RBAC rules ----
    rules = load_rbac_rules(rules_path)

    # ---- Chroma: 2 collections (parents + children) ----
    client = chromadb.PersistentClient(path=db_dir, settings=Settings(anonymized_telemetry=False))
    parents_col = client.get_or_create_collection(name="rt_parents")
    children_col = client.get_or_create_collection(name="rt_children")

    # ---- Embeddings ----
    embedder = SentenceTransformer("all-MiniLM-L6-v2")

    # ---- Session state ----
    session = {
        "session_id": str(uuid.uuid4()),
        "created_at": utc_now_iso(),
        "role": None,
        "allowed_departments": [],
        "history": [],
    }

    print("\n=== Clean RAG with RBAC (Chroma + Gemini) ===")
    print("Type 'help' to see commands.\n")

    # ---- Role selection (RBAC gate) ----
    role = input("Enter role (engineering/hr/legal/operations/security/risk): ").strip()
    allowed_depts = allowed_departments_for_role(rules, role)

    if rules.get("default_deny", True) and not allowed_depts:
        print("\n[RBAC] DENY: role not recognized or no allowed departments configured.")
        return

    session["role"] = role
    session["allowed_departments"] = allowed_depts

    print(f"\n[RBAC] ALLOW: role={role} can access departments={allowed_depts}")

    # ---- Interactive loop ----
    while True:
        raw = input("\nAsk a question (or type a command): ").strip()
        if not raw:
            continue

        cmd = raw.lower()

        # ---- Commands ----
        if cmd == "help":
            print_commands()
            continue

        if cmd in ["exit", "quit"]:
            path = save_session(project_root, session)
            print(f"Saved session to {path}")
            print("Goodbye 👋")
            break

        if cmd == "reset":
            session["history"] = []
            print("Conversation history cleared.")
            continue

        if cmd == "save":
            path = save_session(project_root, session)
            print(f"Saved session to {path}")
            continue

        if cmd.startswith("load "):
            _, sid = raw.split(" ", 1)
            sid = sid.strip()
            try:
                loaded = load_session(project_root, sid)
                session = loaded
                role = session["role"]
                allowed_depts = session["allowed_departments"]
                print(f"Loaded session {sid} (role={role}, allowed={allowed_depts})")
            except Exception as e:
                print(f"Could not load session {sid}: {e}")
            continue

        if cmd == "newsession":
            session = {
                "session_id": str(uuid.uuid4()),
                "created_at": utc_now_iso(),
                "role": role,
                "allowed_departments": allowed_depts,
                "history": [],
            }
            print(f"Started new session: {session['session_id']}")
            continue

        # ====================================================
        # Normal Question Flow
        # ====================================================
        question = raw

        # ---- Add user msg to history ----
        session["history"].append({"role": "user", "text": question})
        session["history"] = trim_history(session["history"])

        # ---- RBAC access questions: answer from rules ----
        if is_access_question(question):
            answer = answer_access_from_rbac(role, allowed_depts)
            print("\n=== Answer ===\n")
            print(answer)

            session["history"].append({"role": "assistant", "text": answer})
            session["history"] = trim_history(session["history"])

            append_audit({
                "ts": utc_now_iso(),
                "session_id": session["session_id"],
                "role": role,
                "allowed_departments": allowed_depts,
                "question": question,
                "mode": "rbac_access",
                "retrieved": [],
                "answer": answer,
            }, project_root)
            continue

        # ---- Retrieve children (RBAC filtered) ----
        retrieved_children = retrieve_children(children_col, embedder, question, allowed_depts, k=TOP_K_CHILD)

        if not retrieved_children:
            answer = "I don't have enough information in the allowed documents."
            print("\n=== Answer ===\n")
            print(answer)

            session["history"].append({"role": "assistant", "text": answer})
            session["history"] = trim_history(session["history"])

            append_audit({
                "ts": utc_now_iso(),
                "session_id": session["session_id"],
                "role": role,
                "allowed_departments": allowed_depts,
                "question": question,
                "mode": "rag",
                "retrieved": [],
                "answer": answer,
            }, project_root)
            continue

        # ---- Developer visibility (optional) ----
        print("\n[Retrieval] Top child matches:")
        for i, r in enumerate(retrieved_children[:6], start=1):
            m = r["metadata"]
            print(
                f"  {i}. dist={r['distance']:.4f} dept={m.get('department')} source={m.get('source')} "
                f"parent_id={m.get('parent_id')} parent={m.get('parent_index')} child={m.get('child_index')}"
            )

        # ---- Fetch parent texts to build coherent context ----
        context_blocks, citations = build_parent_context_from_ids(
            parents_col, retrieved_children, max_parents=MAX_PARENTS_IN_CONTEXT
        )

        if not context_blocks:
            answer = "I don't have enough information in the allowed documents."
            print("\n=== Answer ===\n")
            print(answer)

            session["history"].append({"role": "assistant", "text": answer})
            session["history"] = trim_history(session["history"])

            append_audit({
                "ts": utc_now_iso(),
                "session_id": session["session_id"],
                "role": role,
                "allowed_departments": allowed_depts,
                "question": question,
                "mode": "rag",
                "retrieved": citations,
                "answer": answer,
            }, project_root)
            continue

        # ---- Build prompt with conversation history (bounded) ----
        # Use history excluding current user message if you prefer. Here we exclude it:
        prompt = build_prompt(question, allowed_depts, session["history"][:-1], context_blocks)

        # ---- Gemini generation ----
        resp = model.generate_content(prompt)
        answer = (resp.text or "").strip()

        print("\n=== Answer ===\n")
        print(answer)

        # ---- Add assistant msg to history ----
        session["history"].append({"role": "assistant", "text": answer})
        session["history"] = trim_history(session["history"])

        # ---- Audit log ----
        append_audit({
            "ts": utc_now_iso(),
            "session_id": session["session_id"],
            "role": role,
            "allowed_departments": allowed_depts,
            "question": question,
            "mode": "rag",
            "retrieved": citations,   # parent-level citations
            "answer": answer,
        }, project_root)


if __name__ == "__main__":
    main()
