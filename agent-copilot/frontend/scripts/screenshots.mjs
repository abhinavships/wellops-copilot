// Usage: NEXT_PUBLIC_MOCK=1 npm run dev (port 3100) then: node scripts/screenshots.mjs [outDir] [baseUrl]
import { chromium } from "playwright";
import fs from "node:fs";

const out = process.argv[2] ?? "./shots";
const base = process.argv[3] ?? "http://localhost:3100";
fs.mkdirSync(out, { recursive: true });
const ids = ["a", "b", "c", "d", "e", "f", "g", "h", "i"];
const browser = await chromium.launch();

async function shot(id, { w, h, scheme, tag }) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("pageerror", id, e.message));
  await page.goto(base);
  await page.getByTestId(`suggest-${id}`).click();
  await page.waitForSelector('[data-testid="turn"][data-status="done"]', { timeout: 20000 });
  await page.waitForTimeout(id === "d" ? 3500 : 800);
  await page.screenshot({ path: `${out}/${tag}-${id}.png`, fullPage: false });
  await ctx.close();
}
for (const id of ids) await shot(id, { w: 1440, h: 1000, scheme: "light", tag: "desktop" });
for (const id of ["b", "g", "h"]) await shot(id, { w: 1440, h: 1000, scheme: "dark", tag: "dark" });
for (const id of ["a", "b", "d", "g", "i"]) await shot(id, { w: 390, h: 844, scheme: "light", tag: "mobile" });
await browser.close();
