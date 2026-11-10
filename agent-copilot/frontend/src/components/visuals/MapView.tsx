"use client";
import { useMemo } from "react";
import type { VisualProps } from ".";
import { WellMap } from "./WellMap";
import { asStrArr, fmtVal, num, titleCase } from "@/lib/format";
import { convexHull, locLines, locStatus, padRing, useLocations, type MapPin, type WellLoc } from "@/lib/geo";

const SKIP = /(^lat$|^lon$|_overlay$|^explanation$|^reason_summary$|note|^sql$|^span$|_user_id$|^delta_rule$)/;
const BAD_STATUS = /not[ _]producing|^down$|shut|inactive|^false$/i;

/** Row -> is this a problem item (not producing, or a real spill)? */
function isBad(r: Record<string, unknown>, spillRows: boolean): boolean {
  if (spillRows) return true;
  if (r.producing === false || r.reportable === true) return true;
  for (const c of ["status", "state", "effective_status"]) if (typeof r[c] === "string" && BAD_STATUS.test(r[c] as string)) return true;
  return false;
}

/** Area outline + faded context pins for the area's other wells. */
export function areaContext(areaLocs: WellLoc[] | null, exclude: Set<string>): { outline: [number, number][]; context: MapPin[] } {
  if (!areaLocs?.length) return { outline: [], context: [] };
  const outline = padRing(convexHull(areaLocs.map((l) => [l.lon, l.lat])), 0.1);
  const context = areaLocs.filter((l) => !exclude.has(l.asset_id)).map((l): MapPin => ({ id: l.asset_id, lat: l.lat, lon: l.lon, tone: "faded", lines: locLines(l) }));
  return { outline, context };
}

/** `map` visual (and the "Map" view of tables/bars): rows with lat/lon, or asset ids resolved via /api/wells/locations. */
export function MapView({ visual, data, rows }: VisualProps) {
  const e = visual.encodings ?? {};
  const cols = useMemo(() => data?.columns ?? Object.keys(rows[0] ?? {}), [data, rows]);
  const latC = typeof e.lat === "string" ? e.lat : "lat", lonC = typeof e.lon === "string" ? e.lon : "lon";
  const idC = typeof e.label === "string" ? e.label : cols.includes("asset_id") ? "asset_id" : cols[0] ?? "asset_id";
  const spillRows = data?.shape === "geo_points" || cols.includes("spill_class") || cols.includes("volume_bbl");
  const sizeC = useMemo(() => {
    if (typeof e.size === "string") return e.size;
    if (cols.includes("volume_bbl")) return "volume_bbl";
    const y = asStrArr(e.y)[0];
    if (y && cols.includes(y)) return y;
    if (data?.shape === "category_measure") return cols.find((c) => c !== idC && rows.some((r) => typeof r[c] === "number") && !/(^lat$|^lon$|_id$)/.test(c)) ?? null;
    return null;
  }, [e.size, e.y, cols, data?.shape, idC, rows]);
  const ids = useMemo(() => [...new Set(rows.map((r) => r.asset_id).filter((x): x is string => typeof x === "string"))], [rows]);
  const area = useMemo(() => {
    if (typeof e.area === "string") return e.area;
    const names = [...new Set(rows.map((r) => r.area ?? r.area_name).filter((x): x is string => typeof x === "string" && !!x))];
    return names.length === 1 ? names[0] : null;
  }, [e.area, rows]);
  const locs = useLocations(ids);
  const areaLocs = useLocations([], area);

  const { pins, outline } = useMemo(() => {
    const byId = new Map((locs ?? []).map((l) => [l.asset_id, l]));
    const peak = sizeC && !spillRows && rows.length > 1 ? Math.max(...rows.map((r) => num(r[sizeC]) ?? -Infinity)) : null;
    const shown = cols.filter((c) => c !== idC && !SKIP.test(c) && c !== "asset_id").slice(0, 5);
    const main: MapPin[] = rows.flatMap((r) => {
      const loc = typeof r.asset_id === "string" ? byId.get(r.asset_id) : undefined;
      const lat = num(r[latC]) ?? loc?.lat ?? null, lon = num(r[lonC]) ?? loc?.lon ?? null;
      if (lat === null || lon === null) return [];
      const size = sizeC ? num(r[sizeC]) ?? 0 : undefined;
      const bad = isBad(r, spillRows) || (!spillRows && loc && locStatus(loc) === "not producing" && !cols.some((c) => /status|producing/.test(c)));
      const tone: MapPin["tone"] = peak !== null && size === peak ? "hl" : bad ? "bad" : rows.length === 1 ? "hl" : "ink";
      const id = String(r[idC] ?? r.asset_id ?? "");
      const rowLines = shown.map((c) => `${titleCase(c)}: ${fmtVal(r[c])}`).filter((l) => l.length < 80);
      const extra = loc ? locLines(loc).filter((l) => !rowLines.some((x) => x.includes(l))) : [];
      return [{ id, title: typeof r.asset_id === "string" && idC !== "asset_id" ? r.asset_id : id, lat, lon, tone, size, lines: [...(idC !== "asset_id" && typeof r.asset_id === "string" ? [r.asset_id] : []), ...rowLines, ...extra] }];
    });
    const ctx = areaContext(areaLocs, new Set(ids));
    return { pins: [...ctx.context, ...main], outline: ctx.outline };
  }, [rows, locs, areaLocs, ids, cols, idC, latC, lonC, sizeC, spillRows]);

  const nMain = pins.filter((p) => p.tone !== "faded").length;
  const needsLookup = rows.some((r) => num(r[latC]) === null) && locs === null;
  if (needsLookup) return <div className="h-72 animate-pulse rounded-lg bg-raised" role="status" aria-label="Loading locations" />;
  const caption = [`${nMain} location${nMain === 1 ? "" : "s"}`, sizeC ? `size = ${titleCase(sizeC)}` : "", area && outline.length ? `outline = ${area}` : ""].filter(Boolean).join(" · ");
  return <WellMap pins={pins} outline={outline} caption={caption} ariaLabel={visual.title ?? "Map of well locations"} />;
}
