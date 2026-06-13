/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        lab: {
          bg: "#070a12",
          panel: "rgba(13,20,33,0.72)",
          edge: "rgba(86,180,255,0.18)",
          cyan: "#38e8ff",
          green: "#00ff95",
          amber: "#ffb01f",
          red: "#ff4d6d",
          dim: "#7c8aa5",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
}
