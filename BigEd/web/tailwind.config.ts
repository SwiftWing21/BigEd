import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: { DEFAULT: "#1a1a1a", 2: "#242424", 3: "#2d2d2d" },
        accent: { DEFAULT: "#b22222", hover: "#8b0000" },
        gold: "#c8a84b",
        text: { DEFAULT: "#e2e2e2", dim: "#888888" },
        status: {
          green: "#4caf50",
          orange: "#ff9800",
          red: "#f44336",
          blue: "#2196f3",
          cyan: "#00bcd4",
        },
        glass: {
          bg: "#1e1e1e",
          nav: "#161616",
          panel: "#1a1a1a",
          hover: "#2a2a2a",
          sel: "#2d2d2d",
          border: "#333333",
        },
      },
      fontFamily: {
        mono: ["Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
