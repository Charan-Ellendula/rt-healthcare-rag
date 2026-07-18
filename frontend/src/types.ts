export type ChatRole = "user" | "assistant";

export interface Citation {
  n: number;
  source: string | null;
  department: string | null;
  parent_index: number | null;
}

export interface ChatMessage {
  role: ChatRole;
  text: string;
  citations?: Citation[];
}

export interface Session {
  session_id: string;
  username: string;
  role: string;
  allowed_departments: string[];
}
