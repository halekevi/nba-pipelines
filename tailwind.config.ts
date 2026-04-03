import type { Config } from "tailwindcss"

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#050505",
        foreground: "#ffffff",
        gold: {
          DEFAULT: "#d4af37",
          muted: "rgba(212, 175, 55, 0.15)",
        },
        glass: {
          DEFAULT: "rgba(255, 255, 255, 0.03)",
          mid: "rgba(255, 255, 255, 0.055)",
          hi: "rgba(255, 255, 255, 0.08)",
          border: "rgba(255, 255, 255, 0.09)",
        },
        muted: {
          DEFAULT: "rgba(255, 255, 255, 0.45)",
          dim: "rgba(255, 255, 255, 0.25)",
        },
        status: {
          green: "#39ff6e",
          red: "#ff4d4d",
          cyan: "#00e5ff",
          pending: "#666666",
          amber: "#f0a500",
        },
        sport: {
          nba: "#f0a500",
          "nba-1h": "#ffb27d",
          "nba-1q": "#ffd87a",
          cbb: "#00e5ff",
          wcbb: "#9fd8e8",
          nhl: "#c4a5ff",
          mlb: "#ff9a9a",
          soccer: "#e8b84a",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      backdropBlur: {
        glass: "22px",
      },
      boxShadow: {
        glass: "0 8px 32px rgba(0, 0, 0, 0.45), 0 1px 0 rgba(255, 255, 255, 0.06) inset",
        "glass-lg": "0 20px 60px rgba(0, 0, 0, 0.55), 0 1px 0 rgba(255, 255, 255, 0.07) inset",
      },
    },
  },
  plugins: [],
}

export default config
