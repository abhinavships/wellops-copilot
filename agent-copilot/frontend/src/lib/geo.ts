import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

/** One row of GET /api/wells/locations. */
export interface WellLoc {
  asset_id: string; name: string | null; area_id: string | null; area: string | null; lat: number; lon: number;
  recorded_status: string | null; last_inspected_at?: string | null; last_delta_c?: number | null;
  threshold_c?: number | null; producing?: boolean | null;
}

/** A pin on a WellMap. tone: ink (default), hl (the answer's item, amber), bad (not producing / real spill), faded (context). */
export type PinTone = "ink" | "hl" | "bad" | "faded";
export interface MapPin { id: string; lat: number; lon: number; tone: PinTone; size?: number; title?: string; lines?: string[] }

const cache = new Map<string, Promise<WellLoc[]>>();

/** Locations by ids and/or area. Cached per query; never throws (a failed lookup is an empty list). Works in mock
 * mode too when a backend is reachable, so the maps still show real coordinates. */
export function fetchLocations(q: { ids?: string[]; area?: string | null }): Promise<WellLoc[]> {
  const ids = [...new Set((q.ids ?? []).filter(Boolean))].sort();
  if (!ids.length && !q.area) return Promise.resolve([]);
  const params = new URLSearchParams();
  if (ids.length) params.set("ids", ids.join(","));
  if (q.area) params.set("area", q.area);
  const key = params.toString();
  let p = cache.get(key);
  if (!p) {
    p = fetch(`${API_BASE}/api/wells/locations?${key}`)
      .then((r) => (r.ok ? r.json() : { wells: [] }))
      .then((j: { wells?: WellLoc[] }) => j.wells ?? [])
      .catch(() => { cache.delete(key); return []; });
    cache.set(key, p);
  }
  return p;
}

/** React hook over fetchLocations; `null` while loading. Pass a stable key (ids joined) to avoid refetching. */
export function useLocations(ids: string[], area?: string | null): WellLoc[] | null {
  const idKey = [...new Set(ids)].sort().join(",");
  const key = `${idKey}|${area ?? ""}`;
  const [got, setGot] = useState<{ key: string; locs: WellLoc[] } | null>(null);
  useEffect(() => {
    let live = true;
    fetchLocations({ ids: idKey ? idKey.split(",") : [], area }).then((locs) => live && setGot({ key, locs }));
    return () => { live = false; };
  }, [idKey, area, key]);
  return got && got.key === key ? got.locs : null;
}

/** Convex hull (Andrew's monotone chain) of [lon, lat] points; closed ring, or [] for fewer than 3 distinct points. */
export function convexHull(pts: [number, number][]): [number, number][] {
  const p = [...new Map(pts.map((x) => [`${x[0]},${x[1]}`, x])).values()].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return [];
  const cross = (o: number[], a: number[], b: number[]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [], upper: [number, number][] = [];
  for (const x of p) { while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], x) <= 0) lower.pop(); lower.push(x); }
  for (const x of [...p].reverse()) { while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], x) <= 0) upper.pop(); upper.push(x); }
  const ring = [...lower.slice(0, -1), ...upper.slice(0, -1)];
  return ring.length >= 3 ? [...ring, ring[0]] : [];
}

/** Pad a hull outward from its centroid so the outline does not cut through edge pins. */
export function padRing(ring: [number, number][], frac = 0.08): [number, number][] {
  if (!ring.length) return ring;
  const cx = ring.reduce((s, p) => s + p[0], 0) / ring.length, cy = ring.reduce((s, p) => s + p[1], 0) / ring.length;
  return ring.map(([x, y]) => [x + (x - cx) * frac, y + (y - cy) * frac]);
}

/** "Open in OpenStreetMap" for a set of points: the single point marked, else the centre at a zoom that fits. */
export function osmLink(pts: { lat: number; lon: number }[]): string {
  if (!pts.length) return "https://www.openstreetmap.org/";
  if (pts.length === 1) { const { lat, lon } = pts[0]; return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=14/${lat}/${lon}`; }
  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
  const span = Math.max(Math.max(...lats) - Math.min(...lats), Math.max(...lons) - Math.min(...lons), 1e-3);
  const z = Math.max(3, Math.min(15, Math.floor(Math.log2(360 / span)) - 1));
  const lat = ((Math.min(...lats) + Math.max(...lats)) / 2).toFixed(5), lon = ((Math.min(...lons) + Math.max(...lons)) / 2).toFixed(5);
  return `https://www.openstreetmap.org/#map=${z}/${lat}/${lon}`;
}

/** Status words for a location (producing per the latest reading wins over the monthly recorded status). */
export function locStatus(l: WellLoc): string | null {
  if (typeof l.producing === "boolean") return l.producing ? "producing" : "not producing";
  return l.recorded_status;
}

export function locLines(l: WellLoc): string[] {
  const out: string[] = [];
  if (l.name) out.push(l.name);
  if (l.area) out.push(l.area);
  const st = locStatus(l);
  if (st) out.push(`Status: ${st}${l.recorded_status && st !== l.recorded_status ? ` (recorded: ${l.recorded_status})` : ""}`);
  if (l.last_inspected_at) out.push(`Last reading ${l.last_inspected_at.slice(0, 10)}${typeof l.last_delta_c === "number" ? ` · Δ ${l.last_delta_c}°C` : ""}${typeof l.threshold_c === "number" ? ` (min ${l.threshold_c})` : ""}`);
  return out;
}
