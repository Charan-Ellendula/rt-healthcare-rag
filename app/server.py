# app/server.py
# ============================================================
# FastAPI backend for the React frontend.
# Wraps the RAG + RBAC logic in app/core.py behind a small HTTP API:
#   POST /api/login   -> authenticate, create a session
#   POST /api/chat     -> ask a question within a session
#   POST /api/logout   -> destroy a session
#   GET  /api/me        -> session info (for page refresh)
#
# Sessions are kept in-memory (fine for a single-process demo).
# ============================================================

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core import PROJECT_ROOT, RagRuntime, allowed_departments_for_role, trim_history

AUDIT_LOG_PATH = os.path.join(PROJECT_ROOT, "audit_log.jsonl")

app = FastAPI(title="RT Healthcare RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

runtime: Optional[RagRuntime] = None
sessions: Dict[str, Dict[str, Any]] = {}


@app.on_event("startup")
def _startup() -> None:
    global runtime
    runtime = RagRuntime()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_audit(record: Dict[str, Any]) -> None:
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_session(session_id: str) -> Dict[str, Any]:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return session


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    session_id: str
    username: str
    role: str
    allowed_departments: List[str]


class ChatRequest(BaseModel):
    session_id: str
    question: str


class Citation(BaseModel):
    n: int
    source: Optional[str] = None
    department: Optional[str] = None
    parent_index: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation]


@app.post("/api/login", response_model=LoginResponse)
def login(req: LoginRequest) -> LoginResponse:
    assert runtime is not None
    user = runtime.users.get(req.username)
    if not user or user.get("password") != req.password:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    role = user.get("role")
    allowed_depts = allowed_departments_for_role(runtime.rules, role)

    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "username": req.username,
        "role": role,
        "allowed_departments": allowed_depts,
        "history": [],
    }

    return LoginResponse(
        session_id=session_id,
        username=req.username,
        role=role,
        allowed_departments=allowed_depts,
    )


@app.post("/api/logout")
def logout(session_id: str) -> Dict[str, bool]:
    sessions.pop(session_id, None)
    return {"ok": True}


@app.get("/api/me")
def me(session_id: str) -> Dict[str, Any]:
    session = get_session(session_id)
    return {
        "username": session["username"],
        "role": session["role"],
        "allowed_departments": session["allowed_departments"],
        "history": session["history"],
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    assert runtime is not None
    session = get_session(req.session_id)

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    session["history"].append({"role": "user", "text": question})
    session["history"] = trim_history(session["history"])

    try:
        answer, citations = runtime.answer(
            question=question,
            allowed_depts=session["allowed_departments"],
            history=session["history"][:-1],
        )
    except Exception as exc:
        session["history"].pop()
        raise HTTPException(status_code=502, detail=f"LLM generation failed: {exc}") from exc

    session["history"].append({"role": "assistant", "text": answer})
    session["history"] = trim_history(session["history"])

    append_audit(
        {
            "ts": utc_now_iso(),
            "session_id": req.session_id,
            "username": session["username"],
            "role": session["role"],
            "allowed_departments": session["allowed_departments"],
            "question": question,
            "retrieved": citations,
            "answer": answer,
        }
    )

    return ChatResponse(answer=answer, citations=citations)
