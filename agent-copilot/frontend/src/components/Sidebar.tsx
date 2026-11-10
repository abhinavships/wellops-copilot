"use client";
import { useState } from "react";
import type { ChatSummary, User } from "@/contracts";
import { roleLabel, teamLabel } from "@/lib/format";

export function Sidebar(p: {
  users: User[]; userId: string; onUser: (id: string) => void;
  chats: ChatSummary[]; chatId: string; onChat: (id: string) => void; onNew: () => void;
  today: string; resetToday?: string; onToday: (d: string) => void; mock: boolean;
}) {
  const [open, setOpen] = useState(false);
  const me = p.users.find((u) => u.user_id === p.userId);
  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-3">
      <div className="flex items-center gap-2 px-1">
        <span aria-hidden className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-sm font-bold text-accent-fg">W</span>
        <div><div className="text-sm font-semibold leading-4">WellOps Copilot</div><div className="text-[11px] text-muted">Asset Intelligence{p.mock ? " · mock data" : ""}</div></div>
      </div>

      <section aria-label="User">
        <h2 className="mb-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Acting as</h2>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls="user-list"
          className="flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-panel px-3 py-2 text-left hover:border-accent">
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold">{me?.name ?? p.userId}</span>
            <span className="block truncate text-xs text-muted">{me ? `${roleLabel(me.role)} · ${teamLabel(me.team_id)}` : ""}</span>
          </span>
          <span aria-hidden className="text-muted">{open ? "▴" : "▾"}</span>
        </button>
        {open && (
          <ul id="user-list" className="mt-1 max-h-64 overflow-y-auto rounded-lg border border-line bg-panel py-1">
            {p.users.map((u) => (
              <li key={u.user_id}>
                <button type="button" aria-current={u.user_id === p.userId} onClick={() => { p.onUser(u.user_id); setOpen(false); }}
                  className={`w-full px-3 py-1.5 text-left hover:bg-raised ${u.user_id === p.userId ? "bg-accent/10" : ""}`}>
                  <span className="block text-sm font-medium">{u.name}</span>
                  <span className="block text-[11px] text-muted">{roleLabel(u.role)} · {teamLabel(u.team_id)} · {u.user_id}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Clock">
        <label htmlFor="today" className="mb-1 block px-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Today (simulated clock)</label>
        <input id="today" type="date" value={p.today} onChange={(e) => e.target.value && p.onToday(e.target.value)}
          className="w-full rounded-lg border border-line bg-panel px-3 py-1.5 text-sm" />
        <div className="mt-1 flex gap-1">
          <button type="button" onClick={() => p.onToday(addDays(p.today, 1))} className="rounded-md border border-line px-2 py-0.5 text-xs hover:border-accent">+1 day</button>
          <button type="button" onClick={() => p.onToday(p.resetToday ?? "2026-10-17")} className="rounded-md border border-line px-2 py-0.5 text-xs hover:border-accent">Reset</button>
        </div>
      </section>

      <section aria-label="Chats" className="min-h-0 flex-1">
        <div className="mb-1 flex items-center justify-between px-1">
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-muted">Chats</h2>
          <button type="button" onClick={p.onNew} className="rounded-md bg-accent px-2.5 py-1 text-xs font-semibold text-accent-fg hover:opacity-90">+ New chat</button>
        </div>
        <ul className="space-y-0.5">
          {p.chats.map((c) => (
            <li key={c.chat_id}>
              <button type="button" onClick={() => p.onChat(c.chat_id)} aria-current={c.chat_id === p.chatId}
                className={`w-full truncate rounded-lg px-3 py-1.5 text-left text-sm hover:bg-raised ${c.chat_id === p.chatId ? "bg-raised font-semibold" : ""}`}>
                {c.title || "New chat"}
              </button>
            </li>
          ))}
          {!p.chats.some((c) => c.chat_id === p.chatId) && <li><div className="rounded-lg bg-raised px-3 py-1.5 text-sm font-semibold">New chat</div></li>}
        </ul>
      </section>
    </div>
  );
}

function addDays(d: string, n: number) {
  const t = new Date(`${d}T00:00:00Z`); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10);
}
