// Copies the MapLibre worker bundle into /public so it loads outside the Next bundler (avoids "Worker failed to load").
import fs from "node:fs";
fs.mkdirSync("public/maplibre", { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) fs.copyFileSync(`node_modules/maplibre-gl/dist/${f}`, `public/maplibre/${f}`);
