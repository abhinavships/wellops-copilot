"use client";
import dynamic from "next/dynamic";
import { useCallback, useMemo, useState } from "react";
import type { VisualProps } from ".";
import { fmtVal, num, titleCase, useWidth } from "@/lib/format";

export interface Pt { id: string; lat: number; lon: number; size: number; detail: string }

const GlMap = dynamic(() => import("./GlMap"), { ssr: false, loading: () => <div className="h-72 animate-pulse rounded-lg bg-raised" /> });

export function MapView({ visual, data, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const latC = (typeof e.lat === "string" ? e.lat : "lat"), lonC = (typeof e.lon === "string" ? e.lon : "lon");
  const sizeC = typeof e.size === "string" ? e.size : null;
  const labelC = (typeof e.label === "string" ? e.label : data?.columns[0]) ?? "asset_id";
  const pts: Pt[] = useMemo(() => rows.flatMap((r) => {
    const lat = num(r[latC]), lon = num(r[lonC]);
    if (lat === null || lon === null) return [];
    const extra = (data?.columns ?? []).filter((c) => ![latC, lonC, labelC].includes(c)).map((c) => `${titleCase(c)}: ${fmtVal(r[c])}`).join(" · ");
    return [{ id: String(r[labelC]), lat, lon, size: sizeC ? num(r[sizeC]) ?? 1 : 1, detail: extra }];
  }), [rows, data, latC, lonC, sizeC, labelC]);
  const [fallback, setFallback] = useState(typeof navigator !== "undefined" && navigator.onLine === false);
  const [mode, setMode] = useState<"map" | "plot">(fallback ? "plot" : "map");
  const onFail = useCallback(() => { setFallback(true); setMode("plot"); }, []);
  const sizeLabel = sizeC ? titleCase(sizeC) : "";
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs text-muted">
        <span>{pts.length} location{pts.length === 1 ? "" : "s"}{sizeLabel ? ` · bubble = ${sizeLabel}` : ""}</span>
        <div role="group" aria-label="Map mode" className="flex overflow-hidden rounded-md border border-line">
          {(["map", "plot"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}
              className={`px-2 py-0.5 ${mode === m ? "bg-accent text-accent-fg" : "bg-panel hover:bg-raised"}`}>{m === "map" ? "Map" : "Lat/Lon plot"}</button>
          ))}
        </div>
      </div>
      {mode === "map" && !fallback
        ? <GlMap pts={pts} onFail={onFail} />
        : <SvgScatter pts={pts} />}
      {fallback && <p className="mt-1 text-[11px] text-muted">Basemap unavailable (offline or no WebGL); showing a lat/lon plot.</p>}
    </div>
  );
}

export function SvgScatter({ pts }: { pts: Pt[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [sel, setSel] = useState<string | null>(null);
  const W = Math.max(width, 280), H = Math.min(320, Math.max(220, W * 0.62)), pad = { l: 56, r: 24, t: 16, b: 30 };
  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
  const latMid = (Math.min(...lats) + Math.max(...lats)) / 2, k = Math.cos((latMid * Math.PI) / 180);
  const dLon = Math.max(Math.max(...lons) - Math.min(...lons), 1e-4) * k, dLat = Math.max(Math.max(...lats) - Math.min(...lats), 1e-4);
  const scale = Math.min((W - pad.l - pad.r) / dLon, (H - pad.t - pad.b) / dLat);
  const cx = (Math.min(...lons) + Math.max(...lons)) / 2, cy = (Math.min(...lats) + Math.max(...lats)) / 2;
  const X = (lon: number) => W / 2 + (lon - cx) * k * scale, Y = (lat: number) => H / 2 - (lat - cy) * scale;
  const maxS = Math.max(...pts.map((p) => p.size), 1);
  const r = (s: number) => 7 + Math.sqrt(s / maxS) * 15;
  const selected = pts.find((p) => p.id === sel);
  return (
    <div ref={ref} className="w-full">
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label="Scatter of locations by latitude and longitude" className="rounded-lg bg-raised">
          {[0, 0.5, 1].map((t) => { const lat = cy + (0.5 - t) * ((H - pad.t - pad.b) / scale); return <g key={t}><line x1={pad.l} x2={W - pad.r} y1={Y(lat)} y2={Y(lat)} stroke="rgb(var(--line))" /><text x={pad.l - 6} y={Y(lat) + 4} textAnchor="end" fontSize={10} fill="rgb(var(--muted))">{lat.toFixed(3)}</text></g>; })}
          {[0, 0.5, 1].map((t) => { const lon = cx + (t - 0.5) * ((W - pad.l - pad.r) / (scale * k)); return <g key={t}><line y1={pad.t} y2={H - pad.b} x1={X(lon)} x2={X(lon)} stroke="rgb(var(--line))" /><text x={X(lon)} y={H - 10} textAnchor="middle" fontSize={10} fill="rgb(var(--muted))">{lon.toFixed(3)}</text></g>; })}
          {pts.map((p) => (
            <g key={p.id} tabIndex={0} role="button" aria-label={`${p.id}: ${p.detail}`} onClick={() => setSel(p.id)} onKeyDown={(e) => e.key === "Enter" && setSel(p.id)} style={{ cursor: "pointer" }}>
              <circle cx={X(p.lon)} cy={Y(p.lat)} r={r(p.size)} fill="rgb(var(--bad))" fillOpacity={0.35} stroke="rgb(var(--bad))" strokeWidth={sel === p.id ? 3 : 1.5} />
              <text x={X(p.lon)} y={Y(p.lat) - r(p.size) - 4} textAnchor="middle" fontSize={11} fontWeight={600} fill="rgb(var(--fg))">{p.id}</text>
            </g>
          ))}
        </svg>
      )}
      <p className="mt-1 min-h-4 text-xs text-muted">{selected ? `${selected.id} · ${selected.detail}` : "Select a point for details."}</p>
    </div>
  );
}
