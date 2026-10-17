"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";

/* Voice: speech-to-text runs in the browser (Web Speech API, free); text-to-speech goes through POST /api/tts,
   which caps length and caches clips server-side, so ElevenLabs credits are spent at most once per sentence. */

/** Markdown -> plain text, first `n` sentences (the server caps at 400 chars anyway). */
export function firstSentences(md: string, n = 2): string {
  const plain = md.replace(/```[\s\S]*?```/g, " ").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*(#{1,6}|[-*+]|\d+[.)]|>)\s+/gm, "").replace(/[*_`~|#]/g, " ").replace(/\s+/g, " ").trim();
  return (plain.match(/[^.!?]+[.!?]+(\s|$)/g) ?? [plain]).slice(0, n).join("").trim();
}

let current: HTMLAudioElement | null = null;
export function stopSpeaking() {
  current?.pause(); current = null;
  if (typeof window !== "undefined") window.speechSynthesis?.cancel();
}

/** Free fallback: the browser's own voice (no credits, works offline). */
function speakBrowser(text: string): Promise<void> {
  return new Promise((resolve) => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth) return resolve();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US"; u.rate = 1.05;
    const v = synth.getVoices().find((x) => /en(-|_)US/.test(x.lang) && /Samantha|Google|Natural|Ava|Allison/i.test(x.name));
    if (v) u.voice = v;
    u.onend = () => resolve(); u.onerror = () => resolve();
    synth.speak(u);
  });
}

/** Speak text: ElevenLabs via the backend proxy (cached server-side); on any failure, the browser voice. */
export async function speak(text: string): Promise<void> {
  stopSpeaking();
  if (!text.trim()) return;
  try {
    const r = await fetch(`${API_BASE}/api/tts`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (!r.ok) throw new Error(`TTS HTTP ${r.status}`);
    const url = URL.createObjectURL(await r.blob());
    const a = new Audio(url);
    current = a;
    const played = await new Promise<boolean>((resolve) => {
      const done = (ok: boolean) => () => { URL.revokeObjectURL(url); if (current === a) current = null; resolve(ok); };
      a.onended = done(true); a.onerror = done(false); a.onpause = done(true);
      a.play().catch(done(false)); // autoplay blocked or bad audio -> fall back
    });
    if (played) return;
  } catch { /* fall through to the browser voice */ }
  await speakBrowser(text);
}

// Minimal Web Speech API typings (not in lib.dom for all TS versions).
interface Recognition {
  lang: string; interimResults: boolean; continuous: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null; onerror: ((e: { error: string }) => void) | null;
  start(): void; stop(): void;
}
type RecognitionCtor = new () => Recognition;
const recognitionCtor = (): RecognitionCtor | null => {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

/** Toggle dictation. `onText` receives the live transcript; `onStop` the final one when listening ends. */
export function useDictation(onText: (t: string) => void, onStop: (t: string) => void) {
  const [listening, setListening] = useState(false);
  const [supported, setSupported] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const text = useRef("");
  const cbs = useRef({ onText, onStop });
  useEffect(() => { cbs.current = { onText, onStop }; }, [onText, onStop]);
  useEffect(() => { setSupported(recognitionCtor() !== null); return () => rec.current?.stop(); }, []);

  const start = useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor || rec.current) return;
    stopSpeaking();
    const r = new Ctor();
    r.lang = "en-US"; r.interimResults = true; r.continuous = true;
    text.current = ""; setError(null);
    r.onresult = (e) => {
      text.current = Array.from(e.results, (res) => res[0]?.transcript ?? "").join("").trim();
      cbs.current.onText(text.current);
    };
    r.onerror = (e) => { if (e.error !== "no-speech" && e.error !== "aborted") setError(`Mic: ${e.error}`); };
    r.onend = () => { rec.current = null; setListening(false); cbs.current.onStop(text.current); };
    rec.current = r; setListening(true); r.start();
  }, []);
  const stop = useCallback(() => rec.current?.stop(), []);
  const toggle = useCallback(() => (rec.current ? stop() : start()), [start, stop]);
  return { listening, supported, error, toggle, stop };
}
