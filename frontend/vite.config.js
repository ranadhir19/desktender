import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: the API runs on :8000 (uvicorn app.main:app).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/tenants": "http://localhost:8000",
      "/graphql": "http://localhost:8000",
      "/telephony": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
