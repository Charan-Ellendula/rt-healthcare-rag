# RT Healthcare Secure RAG (RBAC-Enabled)

Enterprise-grade Retrieval-Augmented Generation (RAG) system with Role-Based Access Control (RBAC), audit logging, conversational memory, and parent-child chunking architecture.

Built to demonstrate production-style AI system design in a healthcare governance and compliance environment.

---

## 🚀 Live Demo

This project has migrated from a Streamlit UI to a React + FastAPI stack (see [Local Setup](#️-local-setup)). The previous Streamlit Community Cloud deployment is no longer wired up and will need to be retired/replaced separately.

---

## 🧠 What This Project Demonstrates

This is NOT a basic RAG demo.

It demonstrates:

- Secure Retrieval with RBAC enforcement
- Department-level document access control
- Parent-Child chunking architecture
- Conversational memory
- Audit logging (JSONL format)
- Cloud deployment readiness
- Gemini LLM integration (Flash 2.5)
- Chroma persistent vector database
- Enterprise-style system separation

---

## 🏗 Architecture Overview

User Login (Role Assigned via RBAC)
        ↓
Vector Retrieval (Child Chunks)
        ↓
Parent Chunk Expansion
        ↓
Context Construction
        ↓
Gemini 2.5 Flash
        ↓
Response + Audit Logging

---

## 🔐 Role-Based Access Control (RBAC)

Access is enforced at retrieval time using metadata filters.

Example role permissions:

| Role        | Allowed Departments |
|------------|--------------------|
| engineering | engineering, policies |
| hr          | hr, policies |
| legal       | legal_internal, policies, risk_governance |
| operations  | operations, policies |
| security    | security, risk_governance, policies |
| risk        | risk_governance, policies |

Filtering is applied directly inside Chroma queries:

```python
where={"department": {"$in": allowed_departments}}
```

This ensures restricted documents are never retrieved.

---

## 📚 Parent–Child Chunking Design

### Why?

- Child chunks → better semantic retrieval accuracy
- Parent chunks → better contextual coherence for LLM

### How It Works

1. Documents split into large parent chunks
2. Parent chunks split into smaller child chunks
3. Vector search runs on child chunks
4. Top child matches → corresponding parent chunks retrieved
5. Parent text sent to LLM for structured answer

This reduces hallucination and improves answer quality.

---

## 💬 Conversational Memory

Conversation history is stored using:

```python
st.session_state["history"]
```

Only the most recent N turns are retained to:
- Maintain context continuity
- Prevent token explosion
- Keep responses focused

---

## 📜 Audit Logging

Every interaction logs structured data in append-only format:

```json
{
  "timestamp": "...",
  "session_id": "...",
  "username": "...",
  "role": "...",
  "allowed_departments": [...],
  "question": "...",
  "retrieved_sources": [...],
  "answer": "..."
}
```

Stored in:

```
audit_log.jsonl
```

This simulates enterprise compliance logging requirements.

---

## 🗂 Project Structure

```
rt-healthcare-rag/
│
├── app/
│   ├── core.py     # shared RAG + RBAC logic
│   ├── server.py   # FastAPI backend (REST API)
│   └── main.py     # CLI variant (optional, for local testing)
│
├── frontend/       # React + TypeScript + Tailwind UI
│   └── src/
│       ├── components/
│       ├── api.ts
│       └── App.tsx
│
├── ingestion/
│   ├── __init__.py
│   └── ingest.py
│
├── data/
│   ├── hr/
│   ├── engineering/
│   ├── policies/
│   ├── legal_internal/
│   ├── risk_governance/
│   ├── security/
│   └── ...
│
├── chroma_db/
├── users.yaml
├── rbac_rules.yaml
├── requirements.txt
└── README.md
```

---

## 🛠 Tech Stack

- LLM: Google Gemini 2.5 Flash
- Vector Database: Chroma (Persistent)
- Embeddings: all-MiniLM-L6-v2
- Backend: FastAPI (REST API)
- Frontend: React + TypeScript + Vite + Tailwind CSS
- Auth Layer: YAML-based pseudo-identity
- Language: Python 3.9+ / Node 18+

---

## ⚙️ Local Setup

Clone repository:

```bash
git clone https://github.com/Charan-Ellendula/rt-healthcare-rag.git
cd rt-healthcare-rag
```

### Backend

Create environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `.env` file in the project root:

```
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
```

Start the API server (this also builds the vector index on first run if empty):

```bash
uvicorn app.server:app --port 8000
```

The API is now available at `http://localhost:8000` (docs at `/docs`).

### Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open:

```
http://localhost:5173
```

Log in with one of the demo accounts in [users.yaml](users.yaml) (e.g. `alice_eng` / `Eng@1234`).

---

## 🎯 Why This Matters

This project simulates:

- Enterprise data governance
- Secure LLM access control
- Compliance-ready AI system
- Healthcare-aligned architecture
- Production-style RAG design

It is structured to be:

- Interview-ready
- Architecture-explainable
- Security-focused
- Cloud-deployable

---

## 👤 Author

Saicharan Ellendula  
AI/ML Engineer – Secure RAG Systems  
GitHub: https://github.com/Charan-Ellendula

---

## 📌 Future Improvements

- JWT authentication
- Database-backed identity management
- Role inheritance hierarchy
- Multi-tenant isolation
- Reranking layer
- Guardrail enforcement
- SOC2-ready logging integration
- pgvector production backend

---

## 🏁 Final Note

This is not just a demo.

It is a structured, explainable secure RAG system aligned with enterprise governance principles.
