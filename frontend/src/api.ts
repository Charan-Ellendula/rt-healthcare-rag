import type { Citation, Session } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export function login(username: string, password: string): Promise<Session> {
  return fetch(`${API_BASE}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }).then((res) => handle<Session>(res));
}

export function logout(sessionId: string): Promise<void> {
  return fetch(`${API_BASE}/api/logout?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
  }).then(() => undefined);
}

export interface ChatResult {
  answer: string;
  citations: Citation[];
}

export function askQuestion(sessionId: string, question: string): Promise<ChatResult> {
  return fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, question }),
  }).then((res) => handle<ChatResult>(res));
}
