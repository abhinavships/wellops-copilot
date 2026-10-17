"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatSummary, User } from "@/contracts";
import { DEFAULT_TODAY, MOCK, fetchChats, fetchUsers, type Ctx } from "@/lib/api";
import { uid } from "@/lib/agui";
import { useChat } from "@/lib/chat";
import { SCENARIOS } from "@/mock/fixtures";
import { AnswerView } from "@/components/AnswerView";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { Sidebar } from "@/components/Sidebar";
import { ToolChip } from "@/components/ToolChip";
import { UiContext, type EvidenceTarget } from "@/components/ui";
import { XrayPanel } from "@/components/XrayPanel";
import { Board } from "@/components/Board";
import { MicButton, SpeakButton, VoiceToggle } from "@/components/Voice";
import { firstSentences, speak, useDictation } from "@/lib/voice";

const ls = {
  get: (k: string) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};
const newChatId = () => `chat-${uid().slice(0, 8)}`;

export function Shell() {
  const [users, setUsers] = useState<User[]>([]);
  const [userId, setUserId] = useState("U-201");
  const [today, setToday] = useState(DEFAULT_TODAY);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState(() => newChatId());
  const [theme, setTheme] = useState<"light" | "dark" | "system">("system");
  const [leftOpen, setLeftOpen] = useState(false);
  const [xrayOpen, setXrayOpen] = useState(false);
  const [xrayDesktop, setXrayDesktop] = useState(true);
  const [view, setView] = useState<"chat" | "split" | "board">("chat"); // chat | chat + playboard | playboard
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const [selTurn, setSelTurn] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const ctx: Ctx = useMemo(() => ({ userId, chatId, today }), [userId, chatId, today]);
  const { turns, busy, send, stop } = useChat(ctx);

  // restore preferences
  useEffect(() => {
    const t = ls.get("wellops.theme") as "light" | "dark" | null; if (t) setTheme(t);
    const d = ls.get("wellops.today"); if (d) setToday(d);
    const u = ls.get("wellops.user"); if (u) setUserId(u);
  }, []);
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", theme);
    ls.set("wellops.theme", theme);
  }, [theme]);
  useEffect(() => { fetchUsers().then(setUsers).catch((e: Error) => setLoadErr(`Could not load users: ${e.message}`)); }, []);

  const registryKey = `wellops.chats.${userId}`;
  const loadChats = useCallback(async () => {
    let local: ChatSummary[] = [];
    try { local = JSON.parse(ls.get(registryKey) ?? "[]") as ChatSummary[]; } catch { /* ignore */ }
    let remote: ChatSummary[] = [];
    try { remote = await fetchChats(userId); } catch { /* backend offline: local only */ }
    const map = new Map<string, ChatSummary>();
    [...remote, ...local].forEach((c) => map.set(c.chat_id, { ...map.get(c.chat_id), ...c }));
    const all = [...map.values()].sort((a, b) => String(b.updated_at ?? "").localeCompare(String(a.updated_at ?? "")));
    setChats(all); return all;
  }, [userId, registryKey]);
  useEffect(() => { loadChats().then((all) => { if (all[0]) setChatId(all[0].chat_id); else setChatId(newChatId()); }); }, [loadChats]);

  // register chat in the local list once it has a first turn
  const firstText = turns[0]?.userText;
  useEffect(() => {
    if (!firstText) return;
    setChats((prev) => {
      const existing = prev.find((c) => c.chat_id === chatId);
      const title = existing?.title || firstText.slice(0, 48);
      const next = [{ chat_id: chatId, title, updated_at: new Date().toISOString() }, ...prev.filter((c) => c.chat_id !== chatId)];
      ls.set(registryKey, JSON.stringify(next)); return next;
    });
  }, [firstText, chatId, registryKey]);

  useEffect(() => { bottom.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [turns]);
  useEffect(() => { setSelTurn(null); }, [chatId]);

  const submit = useCallback((text: string, scenario?: string) => { const t = text.trim(); if (!t || busy) return; setInput(""); send(t, scenario); }, [busy, send]);
  const ui = useMemo(() => ({ ctx, openEvidence: setEvidence, send: (t: string) => submit(t) }), [ctx, submit]);

  // voice: dictation fills the composer (auto-sends in voice mode); voice mode reads new answers aloud. Off by default.
  const [voiceOn, setVoiceOn] = useState(false);
  const voiceSince = useRef(Infinity);
  const spoken = useRef(new Set<string>());
  const dictation = useDictation(setInput, (t) => { if (voiceOn && t.trim()) submit(t); });
  const last = turns[turns.length - 1];
  useEffect(() => {
    if (!voiceOn || !last?.answer || last.status !== "done" || last.startedAt < voiceSince.current || spoken.current.has(last.id)) return;
    spoken.current.add(last.id);
    speak(firstSentences(last.answer.markdown, 2)).catch(() => { /* voice is best-effort */ });
  }, [voiceOn, last]);
  const toggleVoice = (v: boolean) => { voiceSince.current = v ? Date.now() : Infinity; setVoiceOn(v); };

  const switchUser = (id: string) => { setUserId(id); ls.set("wellops.user", id); setLeftOpen(false); };
  const sidebar = (
    <Sidebar users={users} userId={userId} onUser={switchUser} chats={chats} chatId={chatId} onChat={(id) => { setChatId(id); setLeftOpen(false); }}
      onNew={() => { setChatId(newChatId()); setLeftOpen(false); }} today={today} onToday={(d) => { setToday(d); ls.set("wellops.today", d); }} mock={MOCK} />
  );
  const xray = <XrayPanel turns={turns} selected={selTurn} onSelect={setSelTurn} />;

  return (
    <UiContext.Provider value={ui}>
      <div className="flex h-dvh overflow-hidden">
        <aside className="hidden w-72 shrink-0 border-r border-line bg-panel md:block" aria-label="Sidebar">{sidebar}</aside>
        {leftOpen && (
          <div className="fixed inset-0 z-30 md:hidden">
            <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setLeftOpen(false)} />
            <aside className="relative h-full w-[85%] max-w-xs bg-panel shadow-xl" aria-label="Sidebar">{sidebar}</aside>
          </div>
        )}

        <main className={`min-w-0 flex-col ${view === "board" ? "hidden" : "flex"} ${view === "split" ? "w-[440px] shrink-0 border-r border-line" : "flex-1"}`}>
          <header className="flex items-center gap-2 border-b border-line bg-panel px-3 py-2">
            <button type="button" className="rounded-md border border-line px-2 py-1 text-sm md:hidden" onClick={() => setLeftOpen(true)} aria-label="Open menu">☰</button>
            <h1 className="flex min-w-0 flex-1 items-baseline gap-2 text-sm font-semibold"><span className="truncate">{chats.find((c) => c.chat_id === chatId)?.title || "New chat"}</span><span className="hidden shrink-0 font-normal text-muted sm:inline">as of {today}</span></h1>
            <button type="button" className="rounded-md border border-line px-2 py-1 text-xs hover:border-accent"
              onClick={() => setTheme((t) => (t === "dark" ? "light" : t === "light" ? "system" : "dark"))} aria-label={`Theme: ${theme}. Click to change`}>
              {theme === "dark" ? "Dark" : theme === "light" ? "Light" : "Auto"}
            </button>
            <VoiceToggle on={voiceOn} onChange={toggleVoice} />
            <button type="button" aria-pressed={xrayDesktop && xrayOpen} className="rounded-md border border-line px-2 py-1 text-xs hover:border-accent"
              onClick={() => { if (window.matchMedia("(min-width:1024px)").matches) setXrayDesktop((v) => !v); else setXrayOpen((v) => !v); }}>X-ray</button>
            <button type="button" data-testid="view-toggle" className="rounded-md border border-line px-2 py-1 text-xs hover:border-accent" aria-label={`View: ${view}. Click to change`}
              onClick={() => { const n = view === "chat" ? "split" : view === "split" ? "board" : "chat"; setView(n); if (n !== "chat") setXrayDesktop(false); }}>{view === "chat" ? "Chat" : view === "split" ? "Chat + Board" : "Board"}</button>
          </header>

          <div className="flex-1 overflow-y-auto" role="log" aria-label="Conversation">
            <div className="mx-auto w-full max-w-3xl space-y-6 px-3 py-5 sm:px-5">
              {loadErr && <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 p-3 text-sm text-bad">{loadErr}. Is the backend running at the configured API base? Use NEXT_PUBLIC_MOCK=1 for the demo fixtures.</p>}
              {turns.length === 0 && (
                <div className="py-8">
                  <h2 className="text-xl font-semibold">What do you want to know about the fleet?</h2>
                  <p className="mt-1 text-sm text-muted">Answers are drawn from the data, with every figure traceable to a record. Try one:</p>
                  <div className="mt-4 grid gap-2 sm:grid-cols-2">
                    {SCENARIOS.map((s) => (
                      <button key={s.id} type="button" onClick={() => submit(s.question, s.id)} data-testid={`suggest-${s.id}`}
                        className="rounded-xl border border-line bg-panel p-3 text-left text-sm hover:border-accent">
                        <span className="block text-[11px] font-semibold uppercase tracking-wide text-muted">{s.label}</span>
                        <span className="mt-0.5 block">{s.question}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {turns.map((t) => (
                <article key={t.id} data-testid="turn" data-status={t.status} className="space-y-3" onClick={() => setSelTurn(t.id)}>
                  <div className="flex justify-end"><p className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-sm text-accent-fg">{t.userText}</p></div>
                  {t.tools.length > 0 && <div className="flex flex-wrap gap-1.5" aria-label="Tool calls">{t.tools.map((c) => <ToolChip key={c.id} call={c} />)}</div>}
                  {t.status === "error" && <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 p-3 text-sm text-bad">The run failed: {t.error}</p>}
                  {(t.text || t.answer) ? <AnswerView answer={t.answer} text={t.text} streaming={t.status === "running" && !t.answer} />
                    : t.status === "running" && <p className="text-sm text-muted">Thinking<span className="dots"><span>.</span><span>.</span><span>.</span></span></p>}
                  {t.answer && t.status === "done" && <SpeakButton markdown={t.answer.markdown} />}
                </article>
              ))}
              <div ref={bottom} />
            </div>
          </div>

          <form className="border-t border-line bg-panel p-3" onSubmit={(e) => { e.preventDefault(); submit(input); }}>
            <div className="mx-auto flex max-w-3xl items-end gap-2">
              <label htmlFor="msg" className="sr-only">Message</label>
              <textarea id="msg" rows={1} value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about wells, spills, downtime, obligations…"
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(input); } }}
                className="max-h-32 min-h-10 flex-1 resize-none rounded-xl border border-line bg-bg px-3 py-2 text-sm" />
              <MicButton listening={dictation.listening} supported={dictation.supported} onToggle={dictation.toggle} />
              {busy ? <button type="button" onClick={stop} className="h-10 rounded-xl border border-line px-4 text-sm font-semibold hover:border-bad">Stop</button>
                : <button type="submit" disabled={!input.trim()} className="h-10 rounded-xl bg-accent px-4 text-sm font-semibold text-accent-fg disabled:opacity-50">Send</button>}
            </div>
          </form>
        </main>
        {view !== "chat" && (
          <section className="relative min-w-0 flex-1" aria-label="Playboard">
            {view === "board" && <button type="button" onClick={() => setView("split")} className="absolute right-3 top-2 z-30 rounded-md border border-line bg-panel px-2 py-1 text-xs shadow">Show chat</button>}
            <Board />
          </section>
        )}

        {xrayDesktop && <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-line bg-panel lg:block" aria-label="X-ray panel">
          <div className="border-b border-line px-4 py-2 text-sm font-semibold">X-ray</div>{xray}</aside>}
        {xrayOpen && (
          <div className="fixed inset-0 z-30 lg:hidden">
            <button type="button" aria-label="Close X-ray" className="absolute inset-0 bg-black/40" onClick={() => setXrayOpen(false)} />
            <aside className="absolute inset-y-0 right-0 w-[88%] max-w-sm overflow-y-auto bg-panel shadow-xl" aria-label="X-ray panel">
              <div className="flex items-center justify-between border-b border-line px-4 py-2"><span className="text-sm font-semibold">X-ray</span><button type="button" className="text-sm" onClick={() => setXrayOpen(false)}>Close</button></div>{xray}</aside>
          </div>
        )}
      </div>
      <EvidenceDrawer target={evidence} ctx={ctx} onClose={() => setEvidence(null)} />
    </UiContext.Provider>
  );
}
