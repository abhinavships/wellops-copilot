"use client";
import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { MapPin } from "@/lib/geo";

if (typeof window !== "undefined") maplibregl.setWorkerUrl(`${window.location.origin}/maplibre/maplibre-gl-worker.mjs`);

// OSM raster tiles (tile usage policy: visible attribution, no bulk prefetch, modest zoom ceiling, browser cache only).
const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
// Elevation for the optional 3D tilt: public Terrarium DEM tiles (AWS Open Data), fetched only when tilt is turned on.
const DEM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));

function style(dark: boolean): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: { osm: { type: "raster", tiles: [OSM], tileSize: 256, maxzoom: 18, attribution: "© <a href=\"https://www.openstreetmap.org/copyright\" target=\"_blank\" rel=\"noreferrer\">OpenStreetMap</a> contributors" } },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": dark ? "#15130f" : "#f4f1ea" } },
      // muted, slightly warm basemap so ink pins and the amber highlight carry the figure
      { id: "osm", type: "raster", source: "osm", paint: dark
        ? { "raster-saturation": -0.85, "raster-brightness-max": 0.62, "raster-contrast": 0.05 }
        : { "raster-saturation": -0.65, "raster-contrast": -0.08, "raster-brightness-min": 0.06, "raster-hue-rotate": 8 } },
    ],
  };
}

export interface GlMapProps {
  pins: MapPin[]; outline?: [number, number][]; compact?: boolean; className?: string; ariaLabel?: string;
  onFail: () => void; labels?: boolean;
}

/** MapLibre GL map over OSM raster tiles with paper-styled pins, popups and an optional area outline. */
export default function GlMap({ pins, outline = [], compact = false, className = "h-72", ariaLabel = "Map of well locations", onFail, labels }: GlMapProps) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = useState(false);
  const [tilt, setTilt] = useState(false);
  const failRef = useRef(onFail);
  useEffect(() => { failRef.current = onFail; }, [onFail]);

  // create the map once per mount
  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const dark = document.documentElement.dataset.theme === "dark";
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container: node, style: style(dark), center: [-102.3, 32], zoom: 8, maxZoom: 18, maxPitch: 70,
        attributionControl: { compact }, refreshExpiredTiles: false, scrollZoom: false, dragRotate: !compact,
        pitchWithRotate: !compact, maxTileCacheSize: 256, fadeDuration: 0,
      });
    } catch { failRef.current(); return; }
    mapRef.current = map;
    if (!compact) map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), "top-right");
    let ok = false, errors = 0;
    map.on("data", (e) => { if ((e as unknown as { tile?: unknown; sourceId?: string }).sourceId === "osm" && (e as unknown as { tile?: unknown }).tile) ok = true; });
    map.on("error", (e) => {
      const src = (e as unknown as { sourceId?: string }).sourceId;
      if (src === "dem") return; // terrain is optional; the flat map still works
      if (src === "osm" || !src) { errors++; if (errors >= 3 && !ok) failRef.current(); }
    });
    map.on("webglcontextlost", () => failRef.current());
    const timer = setTimeout(() => { if (!ok) failRef.current(); }, 9000);
    map.on("load", () => setReady(true));
    // the map lives in resizable containers (compare dock, canvas modal, side-by-side): follow the box, not the window
    const ro = new ResizeObserver(() => { if (node.clientWidth && node.clientHeight) map.resize(); });
    ro.observe(node);
    return () => { clearTimeout(timer); ro.disconnect(); map.remove(); mapRef.current = null; setReady(false); };
  }, [compact]);

  // pins, outline and framing
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const markers: maplibregl.Marker[] = [];
    const maxS = Math.max(...pins.map((p) => p.size ?? 1), 1);
    const showLabel = (p: MapPin) => labels ?? (p.tone === "hl" || (p.tone !== "faded" && pins.filter((q) => q.tone !== "faded").length <= 10));
    // faded context pins first so the answer's pins sit on top
    const order = [...pins].sort((a, b) => (a.tone === "faded" ? 0 : a.tone === "ink" ? 1 : 2) - (b.tone === "faded" ? 0 : b.tone === "ink" ? 1 : 2));
    for (const p of order) {
      const base = compact ? 10 : 12;
      const d = p.tone === "faded" ? base - 3 : p.size !== undefined ? base + Math.sqrt((p.size ?? 0) / maxS) * (compact ? 10 : 22) : base + (p.tone === "hl" ? 4 : 0);
      const m = document.createElement("div");
      m.className = `wm-pin wm-${p.tone}`;
      m.style.width = m.style.height = `${d}px`;
      m.tabIndex = p.tone === "faded" ? -1 : 0;
      m.setAttribute("role", "button");
      m.setAttribute("aria-label", [p.id, ...(p.lines ?? [])].join(", "));
      if (showLabel(p) && !(compact && p.tone === "faded")) {
        const lab = document.createElement("span"); lab.className = "wm-label"; lab.textContent = p.title ?? p.id; m.appendChild(lab);
      }
      const html = `<div class="wm-pop"><div class="wm-pop-id">${esc(p.id)}</div>${(p.lines ?? []).map((l) => `<div>${esc(l)}</div>`).join("")}</div>`;
      const mk = new maplibregl.Marker({ element: m }).setLngLat([p.lon, p.lat])
        .setPopup(new maplibregl.Popup({ offset: d / 2 + 4, closeButton: false, maxWidth: "260px" }).setHTML(html)).addTo(map);
      m.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); mk.togglePopup(); } });
      markers.push(mk);
    }
    const ring = outline.length >= 4 ? outline : null;
    const fc = { type: "FeatureCollection" as const, features: ring ? [{ type: "Feature" as const, properties: {}, geometry: { type: "Polygon" as const, coordinates: [ring] } }] : [] };
    const src = map.getSource("area") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData(fc);
    else {
      map.addSource("area", { type: "geojson", data: fc });
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": "#fad65c", "fill-opacity": 0.12 } });
      map.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#181612", "line-width": 1.4, "line-dasharray": [3, 2], "line-opacity": 0.75 } });
    }
    const fitPts: [number, number][] = [...pins.map((p) => [p.lon, p.lat] as [number, number]), ...(ring ?? [])];
    if (fitPts.length) {
      const b = new maplibregl.LngLatBounds(fitPts[0], fitPts[0]);
      fitPts.forEach((x) => b.extend(x));
      const fit = () => map.fitBounds(b, { padding: compact ? 22 : 48, maxZoom: compact ? 13 : 14, duration: 0 });
      fit();
      if (!el.current?.clientWidth) map.once("resize", fit);
    }
    return () => markers.forEach((m) => m.remove());
  }, [pins, outline, ready, compact, labels]);

  // optional 3D tilt with terrain (lazy: the DEM source is only added the first time tilt is turned on)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (tilt) {
      try {
        if (!map.getSource("dem")) map.addSource("dem", { type: "raster-dem", tiles: [DEM], tileSize: 256, maxzoom: 14, encoding: "terrarium", attribution: "Terrain: Mapzen / AWS Open Data" });
        map.setTerrain({ source: "dem", exaggeration: 2 });
      } catch { /* flat map is fine */ }
      map.easeTo({ pitch: 58, bearing: -18, duration: 600 });
    } else {
      try { map.setTerrain(null as unknown as maplibregl.TerrainSpecification); } catch { /* none set */ }
      map.easeTo({ pitch: 0, bearing: 0, duration: 400 });
    }
  }, [tilt, ready]);

  return (
    <div className={`relative w-full overflow-hidden rounded-lg border border-line ${className}`}>
      <div ref={el} className="h-full w-full" role="region" aria-label={ariaLabel} />
      {!compact && ready && (
        <button type="button" onClick={() => setTilt((t) => !t)} aria-pressed={tilt} title="Tilt the map with terrain"
          className={`absolute left-2 top-2 z-10 rounded-md border border-fg/70 px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest shadow-sm ${tilt ? "bg-hl text-fg" : "bg-panel/90 text-fg hover:bg-raised"}`}>
          {tilt ? "2D" : "3D tilt"}
        </button>
      )}
    </div>
  );
}
