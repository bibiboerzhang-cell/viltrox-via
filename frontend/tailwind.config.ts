import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        via: {
          orange: "#ff8f2a",
          cream: "#fff8f0",
          ink: "#131722",
        },
      },
      boxShadow: {
        glass: "0 24px 60px rgba(15, 23, 42, 0.1)",
      },
      borderRadius: {
        cloud: "28px",
      },
    },
  },
  plugins: [],
} satisfies Config;
