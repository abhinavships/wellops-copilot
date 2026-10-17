"use client";
import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Pt } from "./MapView";

if (typeof window !== "undefined") maplibregl.setWorkerUrl(`${window.location.origin}/maplibre/maplibre-gl-worker.mjs`);

export default function GlMap({ pts, onFail }: { pts: Pt[]; onFail: () => void }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || pts.length === 0) return;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container: el.current,
        style: { version: 8, sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19, attribution: "© OpenStreetMap contributors" } },
          layers: [{ id: "bg", type: "background", paint: { "background-color": "#e8ecef" } }, { id: "osm", type: "raster", source: "osm" }] },
        attributionControl: { compact: true },
      });
    } catch { onFail(); return; }
    let ok = false, errors = 0;
    map.on("data", (e) => { if ((e as unknown as { tile?: unknown }).tile) ok = true; });
    map.on("error", (e) => { const src = (e as unknown as { sourceId?: string }).sourceId; if (src === "osm" || !src) { errors++; if (errors >= 2 && !ok) onFail(); } });
    const timer = setTimeout(() => { if (!ok) onFail(); }, 7000);
    const maxS = Math.max(...pts.map((p) => p.size), 1);
    const bounds = new maplibregl.LngLatBounds();
    pts.forEach((p) => {
      const d = 18 + Math.sqrt(p.size / maxS) * 30;
      const m = document.createElement("div");
      m.style.cssText = `width:${d}px;height:${d}px;border-radius:50%;background:rgba(214,40,57,.45);border:2px solid #d62839;cursor:pointer;position:relative`;
      m.style.display = "flex"; m.style.alignItems = "flex-end"; m.style.justifyContent = "center";
      const lab = document.createElement("span"); lab.textContent = p.id; lab.style.cssText = "position:absolute;top:100%;margin-top:2px;font:600 11px system-ui;color:#111;background:rgba(255,255,255,.85);padding:0 4px;border-radius:3px;white-space:nowrap"; m.appendChild(lab);
      m.setAttribute("role", "img"); m.setAttribute("aria-label", `${p.id}: ${p.detail}`);
      new maplibregl.Marker({ element: m }).setLngLat([p.lon, p.lat])
        .setPopup(new maplibregl.Popup({ offset: d / 2, closeButton: false }).setHTML(`<strong>${p.id}</strong><br/><span style="font-size:12px">${p.detail}</span>`)).addTo(map);
      bounds.extend([p.lon, p.lat]);
    });
    map.fitBounds(bounds, { padding: 50, maxZoom: 14, duration: 0 });
    return () => { clearTimeout(timer); map.remove(); };
  }, [pts, onFail]);
  return <div ref={el} className="h-72 w-full overflow-hidden rounded-lg border border-line" aria-label="Map of locations" />;
}
