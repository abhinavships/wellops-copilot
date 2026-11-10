"use client";
import { useMemo, useState } from "react";
import type { VisualProps } from ".";
import { Badge, CitationChip, stateTone } from "@/components/ui";
import { fmtVal, num, titleCase } from "@/lib/format";
import { locLines, useLocations, type MapPin } from "@/lib/geo";
import { WellMap } from "./WellMap";
import { areaContext } from "./MapView";

type Note = { kind?: string; ref?: string; author?: string; text?: string; window?: string; role?: string; status?: string };

/** JSON-looking strings (e.g. `explanation`) become structured values instead of raw text. */
function parseMaybe(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const t = v.trim();
  if (!(t.startsWith("[") || t.startsWith("{"))) return v;
  try { return JSON.parse(t); } catch { return v; }
}

const isNotes = (v: unknown): v is Note[] => Array.isArray(v) && v.every((x) => x && typeof x === "object" && ("text" in x || "ref" in x));

const KPI_COLS: [string, string][] = [["wellhead_c", "Wellhead"], ["ambient_c", "Ambient"], ["threshold_c", "Threshold"]];
const HIDE = new Set(["explanation", "reason_summary", "note", "note_id", "note_text", "wellhead_overlay", "ambient_overlay"]);

export function StatusCard({ visual, data, rows }: VisualProps) {
  const [open, setOpen] = useState(false);
  const e = visual.encodings ?? {};
  const r = rows[0] ?? {};
  const col = (k: string, fb: string) => (typeof e[k] === "string" && (e[k] as string) in r ? (e[k] as string) : fb);
  const titleCol = col("title", "asset_id");
  const title = String(r[titleCol] ?? visual.title ?? "");
  const raw = r[col("status", "producing")] ?? r["state"];
  const status = typeof raw === "boolean" ? (raw ? "producing" : "not producing") : typeof raw === "string" ? raw : null;
  const good = status !== null && /^(producing|active|met|ok)/i.test(status);
  const value = num(r[col("value", "delta_c")]);
  const threshold = num(r[col("threshold", "threshold_c")]);
  const unit = typeof e.unit === "string" && e.unit.length < 4 ? e.unit : "°C";
  const reason = r["reason_summary"] ?? r[col("note", "note")];
  const notes = [r["explanation"], r["notes"]].map(parseMaybe).find(isNotes) ?? [];
  const shown = new Set([titleCol, col("status", "producing"), col("value", "delta_c"), col("threshold", "threshold_c"), ...KPI_COLS.map((k) => k[0])]);
  const extra = (data?.columns ?? []).filter((c) => !shown.has(c) && !HIDE.has(c) && r[c] !== null && r[c] !== undefined && r[c] !== "");
  const flags = (data?.dq_flags ?? []).filter((f) => f.entity_id === title);
  const pct = value !== null && threshold ? Math.min(100, Math.max(0, (value / threshold) * 100)) : null;
  const subtitle = [r["name"], r["area_name"] ?? r["area"], r["well_type"]].filter((x) => typeof x === "string" && x).join(" · ");

  return (
    <div className="flex flex-col gap-4">
      {/* hero */}
      <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border p-4 ${good ? "border-ok/40 bg-ok/10" : status ? "border-bad/40 bg-bad/10" : "border-line bg-raised"}`}>
        <div className="min-w-0">
          <div className="font-mono text-2xl font-semibold tracking-tight">{title}</div>
          {subtitle && <div className="mt-0.5 text-sm text-muted">{subtitle}</div>}
        </div>
        {status && (
          <div className="flex items-center gap-2">
            <span className={`relative flex h-3 w-3`}>
              <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-60 ${good ? "bg-ok" : "bg-bad"}`} />
              <span className={`relative inline-flex h-3 w-3 rounded-full ${good ? "bg-ok" : "bg-bad"}`} />
            </span>
            <Badge tone={stateTone(status)}>{status.toUpperCase()}</Badge>
          </div>
        )}
      </div>

      {/* KPI tiles */}
      {(value !== null || KPI_COLS.some(([c]) => num(r[c]) !== null)) && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {value !== null && (
            <div className="rounded-lg bg-raised p-3">
              <div className="text-[11px] uppercase tracking-wide text-muted">Δ above ambient</div>
              <div className={`mt-0.5 text-2xl font-semibold tabular-nums ${good ? "text-ok" : "text-bad"}`}>{fmtVal(value)}<span className="ml-1 text-sm font-normal text-muted">{unit}</span></div>
              {pct !== null && (
                <div className="mt-2 h-1.5 rounded-full bg-line" role="img" aria-label={`${fmtVal(value)} of ${fmtVal(threshold)} ${unit} threshold`}>
                  <div className={`h-1.5 rounded-full transition-all duration-700 ${good ? "bg-ok" : "bg-bad"}`} style={{ width: `${pct}%` }} />
                </div>
              )}
            </div>
          )}
          {KPI_COLS.filter(([c]) => num(r[c]) !== null && !(c === "threshold_c" && threshold !== null && value === null)).map(([c, label]) => (
            <div key={c} className="rounded-lg bg-raised p-3">
              <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
              <div className="mt-0.5 text-2xl font-semibold tabular-nums">{fmtVal(num(r[c]))}<span className="ml-1 text-sm font-normal text-muted">°C</span></div>
              {c === "threshold_c" && typeof r["delta_rule"] === "string" && <div className="mt-1 truncate text-[11px] text-muted" title={String(r["delta_rule"])}>{String(r["delta_rule"]).replace(/^rule:/, "")}</div>}
            </div>
          ))}
        </div>
      )}

      {/^WELL-/i.test(title) && <LocationMini id={title} good={status === null ? null : good} />}

      {flags.map((f, i) => <p key={i} className="rounded-md border border-warn/40 bg-warn/10 px-3 py-1.5 text-xs text-warn">⚠ {f.detail}</p>)}

      {/* why */}
      {typeof reason === "string" && reason && (
        <div className="rounded-lg border-l-4 border-accent bg-raised px-4 py-3">
          <div className="text-[11px] uppercase tracking-wide text-muted">Why</div>
          <p className="mt-1 text-sm leading-relaxed">{reason}</p>
        </div>
      )}

      {/* notes timeline */}
      {notes.length > 0 && (
        <ol className="relative ml-2 border-l border-line pl-4">
          {notes.map((n, i) => (
            <li key={i} className="mb-3 last:mb-0">
              <span className={`absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ${n.role === "reason" ? "bg-accent" : "bg-muted"}`} />
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                {n.author && <span className="font-medium text-fg">{n.author}</span>}
                {n.window && <span>· {n.window.replace("..open", " → now").replace("..", " → ")}</span>}
                {n.role && <Badge tone={n.role === "reason" ? "info" : "neutral"}>{n.role}</Badge>}
                {n.ref && <CitationChip c={{ kind: "note", id: n.ref }} />}
              </div>
              {n.text && <p className="mt-0.5 text-sm">{n.text}</p>}
            </li>
          ))}
        </ol>
      )}

      {extra.length > 0 && (
        <div>
          <button type="button" onClick={() => setOpen((o) => !o)} className="text-xs text-muted hover:text-accent" aria-expanded={open}>
            {open ? "▾" : "▸"} Details ({extra.length})
          </button>
          {open && (
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              {extra.map((c) => {
                const v = parseMaybe(r[c]);
                return (<div key={c} className="min-w-0"><dt className="text-[11px] uppercase tracking-wide text-muted">{titleCase(c)}</dt><dd className="truncate" title={String(r[c])}>{typeof v === "object" ? (Array.isArray(v) ? `${v.length} items` : "…") : fmtVal(v)}</dd></div>);
              })}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}

/** Compact location map for one well: the well (amber, or red when not producing) over its area's other wells, faded. */
function LocationMini({ id, good }: { id: string; good: boolean | null }) {
  const locs = useLocations([id]);
  const loc = locs?.[0];
  const areaLocs = useLocations([], loc?.area_id ?? null);
  const { pins, outline } = useMemo(() => {
    if (!loc) return { pins: [] as MapPin[], outline: [] as [number, number][] };
    const ctx = areaContext(areaLocs, new Set([id]));
    const tone: MapPin["tone"] = good === false || (good === null && loc.producing === false) ? "bad" : "hl";
    return { pins: [...ctx.context, { id, lat: loc.lat, lon: loc.lon, tone, lines: locLines(loc) }], outline: ctx.outline };
  }, [loc, areaLocs, id, good]);
  if (locs === null) return <div className="h-40 animate-pulse rounded-lg bg-raised" role="status" aria-label="Loading location" />;
  if (!loc) return null;
  const caption = `Location · ${loc.area ?? loc.area_id ?? ""} · ${loc.lat.toFixed(4)}, ${loc.lon.toFixed(4)}`;
  return (
    <div data-testid="status-minimap">
      <WellMap pins={pins} outline={outline} compact caption={caption} ariaLabel={`Location of ${id}`} />
    </div>
  );
}
