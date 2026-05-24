import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "plotly.js/dist/plotly": "plotly.js-dist-min",
    },
  },
  server: { port: 5173, host: "0.0.0.0" },
});
