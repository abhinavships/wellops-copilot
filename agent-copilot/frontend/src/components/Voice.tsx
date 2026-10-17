"use client";
import { useState } from "react";
import { firstSentences, speak, stopSpeaking } from "@/lib/voice";

/** Plays the answer's summary through /api/tts (server-capped and cached). Click again to stop. */
export function SpeakButton({ markdown }: { markdown: string }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [err, setErr] = useState<string | null>(null);
  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (state !== "idle") { stopSpeaking(); setState("idle"); return; }
    setErr(null); setState("loading");
    try {
      const p = speak(firstSentences(markdown, 4));
      setState("playing"); await p;
    } catch (x) { setErr((x as Error).message); }
    setState("idle");
  };
  return (
    <span className="inline-flex items-center gap-2">
      <button type="button" onClick={onClick} data-testid="speak" aria-label={state === "idle" ? "Read answer aloud" : "Stop reading"}
        className="rounded-md border border-line px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-fg">
        {state === "idle" ? "🔊 Listen" : state === "loading" ? "…" : "■ Stop"}
      </button>
      {err && <span role="status" className="text-xs text-bad">{err}</span>}
    </span>
  );
}

export function MicButton({ listening, supported, onToggle }: { listening: boolean; supported: boolean; onToggle: () => void }) {
  if (!supported) return null; // browser has no Web Speech API (e.g. Firefox): typing still works
  return (
    <button type="button" onClick={onToggle} aria-pressed={listening} data-testid="mic"
      aria-label={listening ? "Stop dictation" : "Dictate a question"}
      className={`grid h-9 w-9 place-items-center rounded-full text-sm transition ${listening ? "animate-pulse bg-bad/15 text-bad" : "text-muted hover:bg-raised hover:text-fg"}`}>
      {listening ? "● Stop" : "🎤"}
    </button>
  );
}

export function VoiceToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button type="button" aria-pressed={on} onClick={() => { if (on) stopSpeaking(); onChange(!on); }} data-testid="voice-mode"
      title="Voice mode: dictation sends automatically and new answers are read aloud (first sentences)"
      className={`rounded-md border px-2 py-1 text-xs hover:border-accent ${on ? "border-accent text-accent" : "border-line"}`}>
      Voice {on ? "on" : "off"}
    </button>
  );
}
