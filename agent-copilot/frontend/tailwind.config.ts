import type { Config } from "tailwindcss";

const v = (n: string) => `rgb(var(--${n}) / <alpha-value>)`;
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), panel: v("panel"), raised: v("raised"), line: v("line"),
        fg: v("fg"), muted: v("muted"), accent: v("accent"), "accent-fg": v("accent-fg"),
        ok: v("ok"), warn: v("warn"), bad: v("bad"), info: v("info"),
      },
      fontFamily: { sans: ["var(--font-sans)", "system-ui", "sans-serif"], mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"] },
    },
  },
  plugins: [],
};
export default config;
