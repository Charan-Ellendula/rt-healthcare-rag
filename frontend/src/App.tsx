import { useState } from "react";
import { logout as apiLogout } from "./api";
import ChatWindow from "./components/ChatWindow";
import Login from "./components/Login";
import Sidebar from "./components/Sidebar";
import type { ChatMessage, Session } from "./types";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  function handleLogin(s: Session) {
    setSession(s);
    setMessages([]);
  }

  async function handleLogout() {
    if (session) {
      await apiLogout(session.session_id).catch(() => undefined);
    }
    setSession(null);
    setMessages([]);
  }

  if (!session) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <div className="flex h-full bg-slate-50 dark:bg-slate-950">
      <Sidebar session={session} onLogout={handleLogout} onReset={() => setMessages([])} />
      <ChatWindow session={session} messages={messages} setMessages={setMessages} />
    </div>
  );
}
