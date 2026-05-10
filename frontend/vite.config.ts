import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      "/api": "http://backend:8000",
      "/media": "http://backend:8000",
    },
  },
});

