import type { Session } from "../types";

interface Props {
  session: Session;
  onLogout: () => void;
  onReset: () => void;
}

const ROLE_COLORS: Record<string, string> = {
  engineering: "bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-300",
  hr: "bg-pink-100 text-pink-700 dark:bg-pink-500/15 dark:text-pink-300",
  legal: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300",
  operations: "bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300",
  security: "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300",
  risk: "bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300",
};

export default function Sidebar({ session, onLogout, onReset }: Props) {
  const roleColor = ROLE_COLORS[session.role] ?? "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2.5 border-b border-slate-200 px-5 py-4 dark:border-slate-800">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-teal-600 text-white">
          <svg viewBox="0 0 24 24" fill="none" className="h-4.5 w-4.5">
            <path
              d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6l7-3z"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <div>
          <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">RT Healthcare RAG</div>
          <div className="text-xs text-slate-400">RBAC-secured assistant</div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5">
        <div className="mb-6">
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Signed in as</div>
          <div className="text-sm font-medium text-slate-900 dark:text-slate-100">{session.username}</div>
          <span className={`mt-2 inline-block rounded-full px-2.5 py-1 text-xs font-medium ${roleColor}`}>
            {session.role}
          </span>
        </div>

        <div>
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Allowed departments
          </div>
          <div className="flex flex-wrap gap-1.5">
            {session.allowed_departments.length === 0 && (
              <span className="text-xs text-slate-400">No access configured</span>
            )}
            {session.allowed_departments.map((dept) => (
              <span
                key={dept}
                className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                {dept}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-2 border-t border-slate-200 px-5 py-4 dark:border-slate-800">
        <button
          onClick={onReset}
          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          Clear conversation
        </button>
        <button
          onClick={onLogout}
          className="w-full rounded-lg px-3 py-2 text-sm font-medium text-red-600 transition hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          Log out
        </button>
      </div>
    </aside>
  );
}
