import type { Config } from "tailwindcss";

const v = (n: string) => `rgb(var(--${n}) / <alpha-value>)`;
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), panel: v("panel"), raised: v("raised"), line: v("line"),
        fg: v("fg"), muted: v("muted"), accent: v("accent"), "accent-fg": v("accent-fg"),
        ok: v("ok"), warn: v("warn"), bad: v("bad"), info: v("info"), hl: v("hl"),
      },
      fontFamily: { sans: ["var(--font-sans)", "system-ui", "sans-serif"], display: ["var(--font-display)", "sans-serif"], mono: ["var(--font-mono)", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
};
export default config;
