"use client";
import dynamic from "next/dynamic";
import { useCallback, useState } from "react";
import { osmLink, type MapPin } from "@/lib/geo";
import { useWidth } from "@/lib/format";

const GlMap = dynamic(() => import("./GlMap"), { ssr: false, loading: () => <div className="h-full w-full animate-pulse rounded-lg bg-raised" /> });

function hasWebGL(): boolean {
  if (typeof window === "undefined") return true;
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); } catch { return false; }
}

export interface WellMapProps {
  pins: MapPin[];
  /** closed [lon, lat] ring drawn as the area outline */
  outline?: [number, number][];
  /** small location map (status card): no controls, no legend */
  compact?: boolean;
  /** height class for the map box */
  heightClass?: string;
  caption?: string;
  ariaLabel?: string;
  labels?: boolean;
}

const LEGEND: [MapPin["tone"], string][] = [["hl", "answer"], ["ink", "well"], ["bad", "not producing / spill"], ["faded", "area context"]];

/** The shared map: MapLibre over OpenStreetMap tiles, with an SVG lat/lon plot when WebGL or tiles are unavailable. */
export function WellMap({ pins, outline = [], compact = false, heightClass, caption, ariaLabel, labels }: WellMapProps) {
  const [failed, setFailed] = useState(() => !hasWebGL() || (typeof navigator !== "undefined" && navigator.onLine === false));
  const onFail = useCallback(() => setFailed(true), []);
  const h = heightClass ?? (compact ? "h-40" : "h-72");
  const main = pins.filter((p) => p.tone !== "faded");
  const tones = new Set(pins.map((p) => p.tone));
  if (!pins.length) return <p className="text-sm text-muted">No coordinates for these rows.</p>;
  return (
    <div className="flex flex-col gap-1.5" data-testid="well-map">
      {failed
        ? <SvgPlot pins={pins} outline={outline} compact={compact} />
        : <GlMap pins={pins} outline={outline} compact={compact} className={h} onFail={onFail} ariaLabel={ariaLabel} labels={labels} />}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
        {caption && <span className="label">{caption}</span>}
        {!compact && LEGEND.filter(([t]) => tones.has(t)).map(([t, l]) => (
          <span key={t} className="inline-flex items-center gap-1"><span className={`wm-pin wm-${t} wm-key`} aria-hidden />{l}</span>
        ))}
        {failed && <span>Basemap unavailable (offline or no WebGL): lat/lon plot.</span>}
        <a className="ml-auto underline decoration-line underline-offset-2 hover:text-fg" href={osmLink(main.length ? main : pins)} target="_blank" rel="noreferrer">
          Open in OpenStreetMap ↗
        </a>
      </div>
    </div>
  );
}

const FILL: Record<MapPin["tone"], string> = { ink: "rgb(var(--fg))", hl: "rgb(var(--hl))", bad: "rgb(var(--bad))", faded: "rgb(var(--muted) / .35)" };

/** Equirectangular lat/lon plot with the same pin vocabulary (fallback when the GL map cannot run). */
export function SvgPlot({ pins, outline = [], compact = false }: { pins: MapPin[]; outline?: [number, number][]; compact?: boolean }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [sel, setSel] = useState<string | null>(null);
  const all: [number, number][] = [...pins.map((p) => [p.lon, p.lat] as [number, number]), ...outline];
  const W = Math.max(width, 200), H = compact ? 160 : Math.min(320, Math.max(220, W * 0.6)), pad = compact ? 14 : 40;
  const lats = all.map((p) => p[1]), lons = all.map((p) => p[0]);
  const cy = (Math.min(...lats) + Math.max(...lats)) / 2, cx = (Math.min(...lons) + Math.max(...lons)) / 2;
  const k = Math.cos((cy * Math.PI) / 180);
  const dLon = Math.max(Math.max(...lons) - Math.min(...lons), 1e-3) * k, dLat = Math.max(Math.max(...lats) - Math.min(...lats), 1e-3);
  const scale = Math.min((W - 2 * pad) / dLon, (H - 2 * pad) / dLat);
  const X = (lon: number) => W / 2 + (lon - cx) * k * scale, Y = (lat: number) => H / 2 - (lat - cy) * scale;
  const maxS = Math.max(...pins.map((p) => p.size ?? 1), 1);
  const r = (p: MapPin) => (p.tone === "faded" ? 3 : p.size !== undefined ? 5 + Math.sqrt((p.size ?? 0) / maxS) * 10 : p.tone === "hl" ? 7 : 5);
  const selected = pins.find((p) => p.id === sel);
  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label="Plot of well locations by latitude and longitude" className="paper rounded-lg border border-line">
          {outline.length >= 4 && <polygon points={outline.map(([lo, la]) => `${X(lo)},${Y(la)}`).join(" ")} fill="rgb(var(--hl) / .15)" stroke="rgb(var(--fg))" strokeDasharray="4 3" strokeWidth={1.2} />}
          {[...pins].sort((a, b) => (a.tone === "faded" ? -1 : 0) - (b.tone === "faded" ? -1 : 0)).map((p) => (
            <g key={`${p.id}-${p.lat}-${p.lon}`} tabIndex={p.tone === "faded" ? -1 : 0} role="button" aria-label={[p.id, ...(p.lines ?? [])].join(", ")}
              onClick={() => setSel(p.id)} onKeyDown={(e) => e.key === "Enter" && setSel(p.id)} style={{ cursor: "pointer" }}>
              <circle cx={X(p.lon)} cy={Y(p.lat)} r={r(p)} fill={FILL[p.tone]} fillOpacity={p.tone === "bad" ? 0.8 : 1} stroke={p.tone === "faded" ? "none" : "rgb(var(--fg))"} strokeWidth={sel === p.id ? 2.5 : 1} />
              {!compact && p.tone !== "faded" && pins.length <= 30 && <text x={X(p.lon)} y={Y(p.lat) - r(p) - 3} textAnchor="middle" fontSize={10} fontFamily="var(--font-mono)" fill="rgb(var(--fg))">{p.title ?? p.id}</text>}
            </g>
          ))}
        </svg>
      )}
      {!compact && <p className="mt-1 min-h-4 text-xs text-muted">{selected ? [selected.id, ...(selected.lines ?? [])].join(" · ") : "Select a point for details."}</p>}
    </div>
  );
}
