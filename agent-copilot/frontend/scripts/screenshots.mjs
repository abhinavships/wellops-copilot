// Usage: NEXT_PUBLIC_MOCK=1 npm run dev (port 3100) then: node scripts/screenshots.mjs [outDir] [baseUrl]
import { chromium } from "playwright";
import fs from "node:fs";

const out = process.argv[2] ?? "./shots";
const base = process.argv[3] ?? "http://localhost:3100";
fs.mkdirSync(out, { recursive: true });
// Scenario ids from src/mock/fixtures.json (backend/scripts/make_fixtures.py); override with SHOTS=q2,q3
const ids = (process.env.SHOTS ?? "q2,q3,q6,q7,q8,q12").split(",");
const browser = await chromium.launch();

async function shot(id, { w, h, scheme, tag }) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("pageerror", id, e.message));
  await page.goto(base);
  await page.getByTestId(`suggest-${id}`).click();
  await page.waitForSelector('[data-testid="turn"][data-status="done"]', { timeout: 20000 });
  await page.waitForTimeout(id === "q7" ? 3500 : 800);
  await page.screenshot({ path: `${out}/${tag}-${id}.png`, fullPage: false });
  await ctx.close();
}
for (const id of ids) await shot(id, { w: 1440, h: 1000, scheme: "light", tag: "desktop" });
if (!process.env.SHOTS) {
  for (const id of ["q3", "q12"]) await shot(id, { w: 1440, h: 1000, scheme: "dark", tag: "dark" });
  for (const id of ["q2", "q12"]) await shot(id, { w: 390, h: 844, scheme: "light", tag: "mobile" });
}
await browser.close();
