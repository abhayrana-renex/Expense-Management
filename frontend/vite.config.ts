import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 4200 matches CORS_ORIGINS in backend/.env.
export default defineConfig({
  plugins: [react()],
  server: { port: 4200, strictPort: true },
});
