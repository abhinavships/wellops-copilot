"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatSummary, User } from "@/contracts";
import { DEFAULT_TODAY, MOCK, fetchChats, fetchHandle, fetchUsers, ingestFile, type Ctx } from "@/lib/api";
import { uid } from "@/lib/agui";
import { useChat } from "@/lib/chat";
import { SCENARIOS } from "@/mock/fixtures";
import { AnswerView } from "@/components/AnswerView";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { Sidebar } from "@/components/Sidebar";
import { UiContext, type EvidenceTarget } from "@/components/ui";
import { XrayPanel } from "@/components/XrayPanel";
import { RunTrace } from "@/components/RunTrace";
import { CompareDock } from "@/components/CompareDock";
import { CanvasModal } from "@/components/Board";
import { CanvasToast } from "@/components/CanvasMenu";
import { viewsFor } from "@/components/visuals/Interactive";
import { chartIntent, setCanvasOpen, setViewOverride, useBoard, viewKey } from "@/lib/board";
import { titleCase } from "@/lib/format";
import { MicButton, SpeakButton, VoiceToggle } from "@/components/Voice";
import { firstSentences, speak, stopSpeaking, useDictation } from "@/lib/voice";

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
  const [theme, setTheme] = useState<"light" | "dark" | "system">("light");
  const [leftOpen, setLeftOpen] = useState(false);
  const [xrayOpen, setXrayOpen] = useState(false);
  const [xrayDesktop, setXrayDesktop] = useState(false); // X-ray is opt-in, from the menu
  const [compareOpen, setCompareOpen] = useState(true); // the right-hand compare dock (wide screens)
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [evidence, setEvidence] = useState<EvidenceTarget | null>(null);
  const [selTurn, setSelTurn] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const ctx: Ctx = useMemo(() => ({ userId, chatId, today }), [userId, chatId, today]);
  const { turns, busy, send, stop, addLocal } = useChat(ctx);
  const boardCount = useBoard().length;

  // restore preferences
  useEffect(() => {
    const t = ls.get("wellops.theme") as "light" | "dark" | null; if (t) setTheme(t);
    const d = ls.get("wellops.today"); if (d) setToday(d);
    const u = ls.get("wellops.user"); if (u) setUserId(u);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
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

  // "make it a bar chart" etc. re-renders the latest visual in the client; anything else goes to the agent.
  const switchChart = useCallback(async (t: string, want: string): Promise<boolean> => {
    const lastWithVisual = [...turns].reverse().find((x) => (x.answer?.visuals ?? []).some((v) => v.type !== "none"));
    const vs = (lastWithVisual?.answer?.visuals ?? []).filter((v) => v.type !== "none");
    if (!vs.length) return false;
    const done: string[] = [], options = new Set<string>();
    for (const v of vs) {
      const data = v.handle ? await fetchHandle(v.handle, chatId).catch(() => null) : null;
      const views = viewsFor(v, data);
      views.forEach((x) => options.add(x));
      const target = want === "chart" ? views.find((x) => x !== "table") : views.includes(want) ? want : undefined;
      if (target) { setViewOverride(viewKey(chatId, v), target); done.push(target); }
    }
    addLocal(t, { markdown: done.length ? `Switched to ${done.map((d) => (d === "table" ? "a table" : `a ${d} chart`)).join(" and ")}.`
      : `That view doesn't fit this data. Try ${[...options].map(titleCase).join(", ") || "another question"}.` });
    return true;
  }, [turns, chatId, addLocal]);
  const submit = useCallback((text: string, scenario?: string) => {
    const t = text.trim(); if (!t || busy) return; setInput("");
    const want = scenario ? null : chartIntent(t);
    if (want) { switchChart(t, want).then((ok) => { if (!ok) send(t); }); return; }
    send(t, scenario);
  }, [busy, send, switchChart]);
  const upload = useCallback(async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      try {
        const report = await ingestFile(f, ctx);
        addLocal(`Uploaded ${f.name}`, { markdown: report.summary?.split(/(?<=\.)\s/)[0] ?? `Ingested ${f.name}.`,
          visuals: [{ type: "ingest_report", title: f.name, encodings: { report } }] });
      } catch (e) {
        addLocal(`Uploaded ${f.name}`, { markdown: `Could not ingest ${f.name}: ${(e as Error).message}` });
      }
    }
  }, [ctx, addLocal]);
  const ui = useMemo(() => ({ ctx, openEvidence: setEvidence, send: (t: string) => submit(t) }), [ctx, submit]);

  // voice: dictation fills the composer (auto-sends in voice mode); voice mode reads new answers aloud. Off by default.
  const [voiceOn, setVoiceOn] = useState(false);
  const voiceSince = useRef(Infinity);
  const spoken = useRef(new Set<string>());
  const dictation = useDictation(setInput, (t) => { if (voiceOn && t.trim()) submit(t); });
  const last = turns[turns.length - 1];
  useEffect(() => {
    if (!voiceOn || !last || last.startedAt < voiceSince.current || spoken.current.has(last.id)) return;
    const say = last.answer ? firstSentences(last.answer.markdown, 2) : last.status === "error" ? "Sorry, that run failed." : "";
    if (!say) return;
    spoken.current.add(last.id);
    speak(say).catch(() => { /* voice is best-effort */ });
  }, [voiceOn, last]);
  const toggleVoice = (v: boolean) => {
    voiceSince.current = v ? Date.now() : Infinity; setVoiceOn(v);
    if (v) speak("Voice mode on.").catch(() => {}); else stopSpeaking(); // the click unlocks audio playback in the browser
  };

  const switchUser = (id: string) => { setUserId(id); ls.set("wellops.user", id); setLeftOpen(false); };
  const sidebar = (
    <Sidebar users={users} userId={userId} onUser={switchUser} chats={chats} chatId={chatId} onChat={(id) => { setChatId(id); setLeftOpen(false); }}
      onNew={() => { setChatId(newChatId()); setLeftOpen(false); }} today={today} onToday={(d) => { setToday(d); ls.set("wellops.today", d); }} mock={MOCK} />
  );
  const xray = <XrayPanel turns={turns} selected={selTurn} onSelect={setSelTurn} />;

  const iconBtn = "grid h-9 w-9 place-items-center rounded-full text-muted transition hover:bg-raised hover:text-fg disabled:opacity-40";
  const composer = (
    <form onSubmit={(e) => { e.preventDefault(); submit(input); }}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)}
      onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); }}
      className={`rounded-[30px] border bg-panel p-2 shadow-[0_1px_0_rgb(var(--line)),0_24px_48px_-28px_rgb(0_0_0/.45)] transition focus-within:border-fg/40 ${dragOver ? "border-fg ring-4 ring-hl/60" : "border-line"}`}>
      <label htmlFor="msg" className="sr-only">Message</label>
      <textarea id="msg" rows={2} value={input} onChange={(e) => setInput(e.target.value)} placeholder={dragOver ? "Drop a CSV, PDF or photo to ingest…" : "Ask about a well, an area, spills, downtime…"}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(input); } }}
        className="max-h-48 min-h-14 w-full resize-none bg-transparent px-4 pt-3 text-[16px] outline-none placeholder:text-muted/80" />
      <div className="flex items-center gap-1 px-1">
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files?.length) upload(e.target.files); e.target.value = ""; }} />
        <button type="button" className={iconBtn} onClick={() => fileRef.current?.click()} aria-label="Attach a file" title="Attach a file (or drop it here)">
          <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="M16 9.5 9.6 15.9a4 4 0 0 1-5.7-5.7l6.8-6.8a2.7 2.7 0 0 1 3.8 3.8l-6.7 6.7a1.3 1.3 0 0 1-1.9-1.9L12 6" /></svg>
        </button>
        <MicButton listening={dictation.listening} supported={dictation.supported} onToggle={dictation.toggle} />
        <button type="button" onClick={() => toggleVoice(!voiceOn)} aria-pressed={voiceOn} data-testid="voice-mode"
          title={voiceOn ? "Voice mode on: answers are read aloud, dictation sends automatically" : "Turn on voice mode"}
          className={`flex h-9 items-center gap-1.5 rounded-full px-3 text-[13px] transition ${voiceOn ? "bg-fg text-bg" : "text-muted hover:bg-raised hover:text-fg"}`}>
          <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><path d="M4 8v4h3l4 3V5L7 8H4z" />{voiceOn && <path d="M14 7.5a3.5 3.5 0 0 1 0 5M16 5a7 7 0 0 1 0 10" />}</svg>
          {voiceOn ? "Voice on" : "Voice"}
        </button>
        <span className="flex-1" />
        {busy ? <button type="button" onClick={stop} aria-label="Stop" className="grid h-9 w-9 place-items-center rounded-full bg-fg text-bg"><span className="h-3 w-3 rounded-sm bg-bg" /></button>
          : <button type="submit" disabled={!input.trim()} aria-label="Send" className="grid h-9 w-9 place-items-center rounded-full bg-fg text-bg transition disabled:opacity-25">
              <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M10 16V4M5 9l5-5 5 5" /></svg></button>}
      </div>
    </form>
  );

  return (
    <UiContext.Provider value={ui}>
      <div className="paper flex h-dvh overflow-hidden">
        <aside className="hidden w-64 shrink-0 border-r border-line bg-bg/70 backdrop-blur md:block" aria-label="Sidebar">{sidebar}</aside>
        {leftOpen && (
          <div className="fixed inset-0 z-30 md:hidden">
            <button type="button" aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setLeftOpen(false)} />
            <aside className="relative h-full w-[85%] max-w-xs bg-panel shadow-xl" aria-label="Sidebar">{sidebar}</aside>
          </div>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-line/70 bg-bg/60 px-5 py-3 backdrop-blur">
            <button type="button" className={`${iconBtn} md:hidden`} onClick={() => setLeftOpen(true)} aria-label="Open menu">☰</button>
            <h1 className="label min-w-0 flex-1 truncate"><span className="text-fg">WellOps</span> · case file <span suppressHydrationWarning>{chatId.replace(/^chat-/, "").slice(0, 6).toUpperCase()}</span><span className="hidden sm:inline"> · {chats.find((c) => c.chat_id === chatId)?.title || "new inquiry"}</span></h1>
            <span className="label hidden md:inline">as of {today}</span>
            <button type="button" onClick={() => setCompareOpen((v) => !v)} aria-pressed={compareOpen} data-testid="toggle-compare"
              className={`hidden rounded-full px-3 py-1.5 text-xs transition lg:inline-flex ${compareOpen ? "bg-fg text-bg" : "border border-line text-muted hover:border-fg hover:text-fg"}`}>
              Compare{boardCount ? ` · ${boardCount}` : ""}</button>
            <div className="relative">
              <button type="button" className={iconBtn} aria-label="More" aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)} data-testid="menu">
                <svg viewBox="0 0 20 20" width="18" height="18" fill="currentColor" aria-hidden><circle cx="4" cy="10" r="1.6" /><circle cx="10" cy="10" r="1.6" /><circle cx="16" cy="10" r="1.6" /></svg>
              </button>
              {menuOpen && (
                <div className="absolute right-0 z-30 mt-1 w-56 rounded-2xl border border-line bg-panel p-1.5 text-sm shadow-xl" role="menu" onMouseLeave={() => setMenuOpen(false)}>
                  <button type="button" role="menuitem" className="flex w-full justify-between rounded-xl px-3 py-2 hover:bg-raised"
                    onClick={() => setTheme((t) => (t === "dark" ? "light" : t === "light" ? "system" : "dark"))}>Theme<span className="text-muted">{titleCase(theme)}</span></button>
                  <button type="button" role="menuitem" className="flex w-full justify-between rounded-xl px-3 py-2 hover:bg-raised"
                    onClick={() => { if (window.matchMedia("(min-width:1024px)").matches) setXrayDesktop((v) => !v); else setXrayOpen((v) => !v); setMenuOpen(false); }}>X-ray panel<span className="text-muted">{xrayDesktop ? "On" : "Off"}</span></button>
                  <button type="button" role="menuitem" className="flex w-full justify-between rounded-xl px-3 py-2 hover:bg-raised" onClick={() => { setCanvasOpen(true); setMenuOpen(false); }}>Open canvas<span className="text-muted">{boardCount}</span></button>
                  <div className="flex items-center justify-between rounded-xl px-3 py-2"><span>Voice</span><VoiceToggle on={voiceOn} onChange={toggleVoice} /></div>
                  <div className="px-3 pb-1 pt-2 text-[11px] text-muted">As of {today}{MOCK ? " · fixture data" : ""}</div>
                </div>
              )}
            </div>
          </header>

          <div className="flex-1 overflow-y-auto" role="log" aria-label="Conversation">
            <div className="mx-auto w-full max-w-[720px] space-y-10 px-4 pb-10 pt-4">
              {loadErr && <p role="alert" className="rounded-2xl border border-bad/30 bg-bad/10 p-3 text-sm text-bad">{loadErr}. Is the backend running at the configured API base?</p>}
              {turns.length === 0 && (
                <div className="flex min-h-[68vh] flex-col justify-center">
                  <div className="label flex items-center justify-between border-b border-line pb-2"><span><span className="text-warn">Wellops</span> · asset intelligence</span><span>Fig. 0 — every figure traces to a record</span></div>
                  <div className="mx-auto mt-10 inline-flex items-center gap-2 rounded-full border border-line bg-panel px-1.5 py-1 text-[13px]">
                    <span className="rounded-full bg-fg px-2 py-0.5 font-mono text-[10px] tracking-widest text-bg">NEW</span>
                    <span>Proof before any correction · drag figures to compare</span>
                  </div>
                  <h2 className="display mt-6 text-center text-5xl font-bold leading-[1.02] sm:text-6xl">Ask your <span className="hl">wells</span><br />anything.</h2>
                  <p className="mx-auto mt-4 max-w-md text-center text-[15px] text-muted">Status, downtime, spills, duties. Answers come back as a figure you can touch, with the records behind every number.</p>
                  <div className="mt-8">{composer}</div>
                  <div className="mt-5 flex flex-wrap justify-center gap-2">
                    {SCENARIOS.slice(0, 4).map((s) => (
                      <button key={s.id} type="button" onClick={() => submit(s.question, MOCK ? s.id : undefined)} data-testid={`suggest-${s.id}`} title={s.question}
                        className="rounded-full border border-line bg-panel px-3.5 py-1.5 text-[13px] transition hover:border-fg">{s.label}</button>
                    ))}
                  </div>
                </div>
              )}
              {turns.map((t) => (
                <article key={t.id} data-testid="turn" data-status={t.status} className="turn-in space-y-4" onClick={() => setSelTurn(t.id)}>
                  <div className="flex justify-end"><p className="max-w-[80%] rounded-[22px] rounded-br-md bg-fg px-4 py-2.5 text-[15px] text-bg">{t.userText}</p></div>
                  <RunTrace turn={t} index={1} />
                  {t.status === "error" && <p role="alert" className="rounded-xl border border-bad/40 bg-panel p-3 font-mono text-[13px] text-bad">× run failed — {t.error}</p>}
                  {(t.text || t.answer) ? <AnswerView answer={t.answer} text={t.text} streaming={t.status === "running" && !t.answer} turn={t} />
                    : null}
                  {t.answer && t.status === "done" && voiceOn && <SpeakButton markdown={t.answer.markdown} />}
                </article>
              ))}
              <div ref={bottom} />
            </div>
          </div>

          {turns.length > 0 && <div className="mx-auto w-full max-w-[720px] px-4 pb-4">{composer}</div>}
        </main>

        <CompareDock open={compareOpen && !xrayDesktop} onClose={() => setCompareOpen(false)} />
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
      <CanvasModal />
      <CanvasToast />
      <EvidenceDrawer target={evidence} ctx={ctx} onClose={() => setEvidence(null)} />
    </UiContext.Provider>
  );
}
